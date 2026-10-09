"""Activity log: every tool call, worker job and provider request, with full payloads.

Model (one tree per tool call):

    call     a tool call from Claude Code (MCP) or the CLI   e.g. fanout(summarizer, 7 files)
      job    one unit of work inside the call                 e.g. "Engine.cs [1/3]"
        attempt  one provider/model try (with retries)        e.g. groq/gpt-oss-120b -> 429 -> retry

Storage (under ~/.config/ai-workers/trace/):

    events-YYYYMMDD.jsonl   one JSON object per line: session | start | end | note
    blobs/<sha1>            large payloads (prompts, file contents, responses), deduplicated

Several processes (one MCP server per Claude Code session, plus the CLI) append to the
same file; lines are written under an exclusive flock. Tracing must never break real work,
so every write path swallows its own errors.
"""
import contextlib, contextvars, hashlib, json, os, threading, time, uuid

try:
    import fcntl
except ImportError:  # non-Unix: in-process lock only
    fcntl = None

CONFIG_DIR = os.path.expanduser("~/.config/ai-workers")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
TRACE_DIR = os.path.join(CONFIG_DIR, "trace")
BLOB_DIR = os.path.join(TRACE_DIR, "blobs")

INLINE_MAX = 2000      # payloads up to this many characters are stored inline in the event
PREVIEW_CHARS = 300
DEFAULTS = {"enabled": True, "retention_days": 7, "max_mb": 200}


def settings():
    try:
        with open(CONFIG_FILE) as f:
            user = json.load(f).get("trace") or {}
    except (OSError, ValueError):
        user = {}
    return {**DEFAULTS, **{k: v for k, v in user.items() if k in DEFAULTS}}


SETTINGS = settings()
SESSION = {"sid": uuid.uuid4().hex[:12], "pid": os.getpid(), "source": "lib", "client": None}

_current = contextvars.ContextVar("ai_workers_span", default=None)
_write_lock = threading.Lock()
_session_logged = False
_last_prune = 0.0


def configure(source=None, client=None):
    """Called by the entry point (MCP server or CLI) to label this process's events."""
    if source:
        SESSION["source"] = source
    if client:
        SESSION["client"] = client


# ---------------------------------------------------------------- writing

def _events_path(ts):
    return os.path.join(TRACE_DIR, time.strftime("events-%Y%m%d.jsonl", time.localtime(ts)))


def _write(ev):
    if not SETTINGS["enabled"]:
        return
    try:
        _ensure_session()
        line = json.dumps(ev, ensure_ascii=False, default=str) + "\n"
        os.makedirs(TRACE_DIR, exist_ok=True)
        with _write_lock, open(_events_path(ev["ts"]), "a", encoding="utf-8") as f:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(line)
                f.flush()
            finally:
                if fcntl:
                    fcntl.flock(f, fcntl.LOCK_UN)
        _maybe_prune()
    except Exception:
        pass


def _ensure_session():
    global _session_logged
    if _session_logged:
        return
    _session_logged = True
    _write({"ev": "session", "ts": _now(), **SESSION, "ppid": os.getppid(), "cwd": os.getcwd()})


def _now():
    return round(time.time(), 3)


def blob(text):
    """Payload reference for an event: inline when small, otherwise a deduplicated file."""
    if text is None:
        return None
    text = str(text)
    if len(text) <= INLINE_MAX or not SETTINGS["enabled"]:
        return {"text": text, "size": len(text)}
    digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
    path = os.path.join(BLOB_DIR, digest)
    try:
        try:
            os.utime(path)  # still in use: keep it out of age-based pruning
        except FileNotFoundError:  # new, or pruned meanwhile
            os.makedirs(BLOB_DIR, exist_ok=True)
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
    except Exception:
        pass
    return {"ref": digest, "size": len(text), "preview": text[:PREVIEW_CHARS]}


def read_blob(ref):
    if not ref or not all(c in "0123456789abcdef" for c in ref) or len(ref) != 40:
        return None
    try:
        with open(os.path.join(BLOB_DIR, ref), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


class Span:
    def __init__(self, kind, parent):
        self.kind = kind
        self.id = uuid.uuid4().hex[:16]
        self.parent = parent.id if parent else None
        self.call = parent.call if parent else self.id
        self.t0 = time.time()
        self.end_fields = {"status": "ok"}

    def set(self, **fields):
        """Fields recorded on the span's end event (status, tokens, response ...)."""
        self.end_fields.update(fields)

    def note(self, name, **fields):
        """A point-in-time event inside the span (retry, fallback ...)."""
        _write({"ev": "note", "ts": _now(), "id": self.id, "call": self.call,
                "name": name, **fields})


@contextlib.contextmanager
def span(kind, **fields):
    parent = _current.get()
    sp = Span(kind, parent)
    _write({"ev": "start", "ts": round(sp.t0, 3), "id": sp.id, "parent": sp.parent,
            "call": sp.call, "kind": kind, "sid": SESSION["sid"], **fields})
    token = _current.set(sp)
    try:
        yield sp
    except BaseException as e:
        sp.set(status="error", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        _current.reset(token)
        _write({"ev": "end", "ts": _now(), "id": sp.id, "call": sp.call, "kind": kind,
                "dur": round(time.time() - sp.t0, 3), **sp.end_fields})


def bound(fn):
    """Wrap fn so it runs under the current span in a worker thread (threads do not inherit
    contextvars). Call once per submitted task: a context cannot be entered concurrently."""
    ctx = contextvars.copy_context()
    return lambda *a, **kw: ctx.run(fn, *a, **kw)


# ---------------------------------------------------------------- retention

def _maybe_prune():
    global _last_prune
    if time.time() - _last_prune > 3600:
        _last_prune = time.time()
        threading.Thread(target=prune, daemon=True).start()


def prune(now=None):
    """Delete event files and blobs older than retention_days, then oldest-first down to max_mb."""
    now = now or time.time()
    cutoff = now - SETTINGS["retention_days"] * 86400
    files = []
    for d in (TRACE_DIR, BLOB_DIR):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            if d == TRACE_DIR and not name.startswith("events-"):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
    files.sort()
    total = sum(s for _, s, _ in files)
    limit = SETTINGS["max_mb"] * 1_000_000
    removed = 0
    for mtime, size, p in files:
        if mtime >= cutoff and total <= limit:
            break
        if p == _events_path(now):
            continue  # never delete the file being written today
        try:
            os.remove(p)
            total -= size
            removed += 1
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------- reading

def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError, TypeError):
        return True


class Reader:
    """Incrementally tails all event files and keeps the span trees in memory."""

    MAX_CALLS = 5000

    def __init__(self, trace_dir=None):
        self.dir = trace_dir or TRACE_DIR
        self.offsets, self.partial = {}, {}
        self.sessions, self.spans, self.calls = {}, {}, []
        self.version = 0
        self.lock = threading.Lock()

    def refresh(self):
        """Read new lines; returns the set of call ids that changed."""
        changed = set()
        try:
            names = sorted(n for n in os.listdir(self.dir)
                           if n.startswith("events-") and n.endswith(".jsonl"))
        except OSError:
            return changed
        with self.lock:
            for name in names:
                path = os.path.join(self.dir, name)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                off = self.offsets.get(name, 0)
                if size < off:  # truncated / replaced
                    off = 0
                if size == off:
                    continue
                with open(path, encoding="utf-8", errors="replace") as f:
                    f.seek(off)
                    data = self.partial.pop(name, "") + f.read()
                    self.offsets[name] = f.tell()
                lines = data.split("\n")
                if lines and lines[-1]:
                    self.partial[name] = lines[-1]  # incomplete last line: finish next time
                for line in lines[:-1]:
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    cid = self._apply(ev)
                    if cid:
                        changed.add(cid)
            self._trim()
            if changed:
                self.version += 1
        return changed

    def _apply(self, ev):
        kind = ev.get("ev")
        if kind == "session":
            self.sessions[ev.get("sid")] = ev
            return None
        sid = ev.get("id")
        if kind == "start":
            node = {**ev, "status": "running", "children": [], "notes": []}
            node.pop("ev", None)
            self.spans[sid] = node
            parent = self.spans.get(ev.get("parent"))
            if parent:
                parent["children"].append(sid)
            if ev.get("kind") == "call":
                self.calls.append(sid)
        elif kind == "end":
            node = self.spans.get(sid)
            if node is None:
                return None
            node.update({k: v for k, v in ev.items() if k not in ("ev", "ts", "id", "call", "kind")})
            node["end"] = ev.get("ts")
        elif kind == "note":
            node = self.spans.get(sid)
            if node is None:
                return None
            node["notes"].append({k: v for k, v in ev.items() if k not in ("ev", "id", "call")})
        return ev.get("call")

    def _trim(self):
        drop = len(self.calls) - self.MAX_CALLS
        if drop <= 0:
            return
        for cid in self.calls[:drop]:
            stack = [cid]
            while stack:
                node = self.spans.pop(stack.pop(), None)
                if node:
                    stack.extend(node["children"])
        del self.calls[:drop]

    # -- views (call under self.lock or via the public helpers below)

    def _status(self, node):
        st = node.get("status")
        if st == "running":
            sess = self.sessions.get(node.get("sid")) or {}
            if sess.get("pid") and not _pid_alive(sess["pid"]):
                return "abandoned"
        return st

    def _walk(self, node):
        for cid in node["children"]:
            child = self.spans.get(cid)
            if child:
                yield child
                yield from self._walk(child)

    def summary(self, cid):
        call = self.spans[cid]
        sess = self.sessions.get(call.get("sid")) or {}
        status = self._status(call)
        jobs = attempts = tin = tout = failed_attempts = skipped_attempts = 0
        providers, roles, running, active = [], [], [], []
        for node in self._walk(call):
            if node["kind"] == "job":
                jobs += 1
                if node.get("role") and node["role"] not in roles:
                    roles.append(node["role"])
            elif node["kind"] == "attempt":
                attempts += 1
                tok = node.get("tokens") or {}
                tin += tok.get("in") or 0
                tout += tok.get("out") or 0
                pm = f"{node.get('provider')}/{node.get('model')}"
                if node.get("status") == "ok" and pm not in providers:
                    providers.append(pm)
                if node.get("status") == "error":
                    failed_attempts += 1
                elif node.get("status") == "skipped":
                    skipped_attempts += 1
            if node.get("status") == "running" and status == "running":
                running.append(node["id"])
                if node["kind"] == "attempt":
                    active.append(f"{node.get('provider')}/{node.get('model')}")
        jobs_done = sum(1 for n in self._walk(call)
                        if n["kind"] == "job" and n.get("status") != "running")
        return {
            "id": cid, "tool": call.get("tool"), "ts": call.get("ts"), "end": call.get("end"),
            "dur": call.get("dur"), "status": status, "title": call.get("title"),
            "source": sess.get("source"), "client": sess.get("client"),
            "project": os.path.basename(sess.get("cwd") or "") or None, "pid": sess.get("pid"),
            "jobs": jobs, "jobs_done": jobs_done, "attempts": attempts,
            "failed_attempts": failed_attempts, "skipped_attempts": skipped_attempts, "providers": providers, "roles": roles,
            "tokens": {"in": tin, "out": tout}, "running": running, "active": active,
        }

    def list_calls(self, limit=300):
        with self.lock:
            return [self.summary(cid) for cid in reversed(self.calls[-limit:]) if cid in self.spans]

    def detail(self, cid):
        with self.lock:
            if cid not in self.spans or self.spans[cid].get("kind") != "call":
                return None

            def tree(sid):
                node = dict(self.spans[sid])
                node["status"] = self._status(node) if node["status"] == "running" else node["status"]
                node["children"] = [tree(c) for c in node["children"] if c in self.spans]
                return node

            out = tree(cid)
            out["summary"] = self.summary(cid)
            out["session"] = self.sessions.get(out.get("sid"))
            return out
