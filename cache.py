"""Disk cache for worker results: identical job -> no provider call, no quota spent.

Never raises: any cache failure is silent (the job just runs).
"""
import hashlib, json, os, threading, time

CACHE_DIR = os.path.join(os.path.expanduser("~/.config/ai-workers"), "cache")
CACHE_VERSION = 1
DEFAULT_TTL = 7 * 86400
MAX_FILES = 2000
MAX_BYTES = 100 * 1024 * 1024
PRUNE_EVERY = 50

_lock = threading.Lock()
_puts = 0
_pruned = False


def make_key(**parts):
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"v{CACHE_VERSION}\n{blob}".encode("utf-8")).hexdigest()


def _path(key):
    return os.path.join(CACHE_DIR, key[:2], f"{key}.json")


def get(key, ttl=DEFAULT_TTL):
    path = _path(key)
    try:
        with open(path, encoding="utf-8") as f:
            ent = json.load(f)
        if time.time() - float(ent["ts"]) > ttl:
            try:
                os.remove(path)
            except OSError:
                pass
            return None
        res = ent["res"]
        return dict(res) if isinstance(res, dict) else None
    except Exception:
        return None


def cacheable(res):
    return bool(res and res.get("ok") and not res.get("truncated") and not res.get("warning")
                and (res.get("text") or "").strip())


def put(key, res):
    global _puts, _pruned
    if not cacheable(res):
        return
    try:
        path = _path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stored = {k: v for k, v in res.items() if k != "route"}
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "res": stored}, f, ensure_ascii=False)
        os.replace(tmp, path)
        with _lock:
            _puts += 1
            due = not _pruned or _puts % PRUNE_EVERY == 0
            _pruned = True
        if due:
            prune()
    except Exception:
        pass


def prune():
    """Keep the newest MAX_FILES files and at most MAX_BYTES in total (by mtime)."""
    try:
        files = []
        for d in os.listdir(CACHE_DIR):
            sub = os.path.join(CACHE_DIR, d)
            if not os.path.isdir(sub):
                continue
            for n in os.listdir(sub):
                p = os.path.join(sub, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                files.append((st.st_mtime, st.st_size, p))
        files.sort(reverse=True)
        total = 0
        for i, (_m, size, p) in enumerate(files):
            total += size
            if i >= MAX_FILES or total > MAX_BYTES:
                try:
                    os.remove(p)
                except OSError:
                    pass
    except Exception:
        pass
