#!/usr/bin/env python3
"""ai-workers command line application.

  ai-workers                 open the desktop app (same as `ai-workers app`)
  ai-workers activity [-f]   recent calls / follow live activity (alias: trace)
  ai-workers open            open the dashboard in the browser
  ai-workers usage           remaining usage report
  ai-workers status          organization status (provider + role)
  ai-workers run <role> "<task>" [-f path/glob ...]   single task; piped stdin is the input (ignored with -f)
  ai-workers fanout <role> "<task>" < list.txt      each line is a separate task
  ai-workers fanout <role> "<task>" -f '~/p/**/*.cs' each file is a separate task
  ai-workers models <provider>
  ai-workers serve           run dashboard in foreground
  ai-workers start|stop|restart|logs   systemd service
  ai-workers doctor          installation check
"""
import argparse, concurrent.futures, json, os, shutil, subprocess, sys, threading, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import activity as T
import providers as P
import usage as U

SERVICE = "ai-workers.service"
DEFAULT_PORT = 8765


def port():
    try:
        with open(P.CONFIG_FILE) as f:
            cfg = json.load(f)
        return int(cfg.get("port") or os.environ.get("AI_WORKERS_PORT") or DEFAULT_PORT)
    except Exception:
        return int(os.environ.get("AI_WORKERS_PORT") or DEFAULT_PORT)


def url():
    return f"http://127.0.0.1:{port()}"


def _systemctl(*args, check=False):
    return subprocess.run(["systemctl", "--user", *args],
                          capture_output=True, text=True, check=check)


def service_active():
    return _systemctl("is-active", SERVICE).stdout.strip() == "active"


def dashboard_up():
    try:
        with urllib.request.urlopen(url() + "/api/health", timeout=3):
            return True
    except Exception:
        return False


def _abs(paths):
    """CLI accepts relative path; server expects absolute path."""
    return [os.path.abspath(os.path.expanduser(p)) for p in paths]


# ---------------------------------------------------------------- commands

def cmd_open(a):
    if not dashboard_up():
        if shutil.which("systemctl") and _systemctl("cat", SERVICE).returncode == 0:
            print("dashboard is not running, starting service…")
            _systemctl("start", SERVICE)
            for _ in range(20):
                if dashboard_up():
                    break
                import time
                time.sleep(0.3)
        if not dashboard_up():
            print(f"dashboard is not running. To start: ai-workers serve", file=sys.stderr)
            return 1
    print(url())
    opener = shutil.which("xdg-open") or shutil.which("open")
    if opener and not a.no_browser:
        subprocess.Popen([opener, url()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


def cmd_app(a):
    """Desktop window (GTK + WebKit); falls back to the browser when they are missing."""
    import app
    if not app.available():
        print(f"desktop app unavailable ({app.why_unavailable()}); opening in the browser",
              file=sys.stderr)
        return cmd_open(argparse.Namespace(no_browser=False))
    cmd = [sys.executable, os.path.join(HERE, "app.py")] + (["--inspect"] if a.inspect else [])
    if a.foreground:
        return subprocess.call(cmd)
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return 0


_MARK = {"ok": "\u2713", "error": "\u2717", "partial": "\u25d0", "warning": "!",
         "running": "\u25b6", "abandoned": "\u2205"}


def _fmt_call(s, color):
    import time as _t
    tok = s["tokens"]
    when = _t.strftime("%H:%M:%S", _t.localtime(s["ts"]))
    dur = f"{s['dur']:.1f}s" if s.get("dur") is not None else "…"
    jobs = f"{s['jobs_done']}/{s['jobs']} jobs" if s["jobs"] > 1 else ""
    who = " · ".join(x for x in (s.get("source"), s.get("project")) if x)
    code = {"ok": "32", "error": "31", "partial": "33", "warning": "33", "running": "36"}.get(s["status"], "90")
    mark = _MARK.get(s["status"], "?")
    if color:
        mark = f"\033[{code}m{mark}\033[0m"
    parts = [when, mark, f"{s['tool']:<8}", (s.get("title") or "")[:70], jobs,
             ", ".join(s["providers"][:2]),
             f"{tok['in']}\u2192{tok['out']} tok" if tok["in"] or tok["out"] else "", dur,
             f"[{who}]" if who else ""]
    return "  ".join(p for p in parts if p)


def cmd_activity(a):
    import time as _t
    r = T.Reader()
    r.refresh()
    color = sys.stdout.isatty()
    calls = list(reversed(r.list_calls(limit=a.n)))
    for s in calls:
        print(_fmt_call(s, color))
    if not a.follow:
        if not calls:
            print(f"no activity yet ({T.TRACE_DIR})")
        return 0
    seen = {s["id"]: s["status"] for s in calls}
    try:
        while True:
            _t.sleep(0.5)
            changed = r.refresh()
            if not changed:
                continue
            for s in reversed(r.list_calls(limit=50)):
                if s["id"] in changed and seen.get(s["id"]) != s["status"]:
                    seen[s["id"]] = s["status"]
                    print(_fmt_call(s, color), flush=True)
    except KeyboardInterrupt:
        return 0


def cmd_usage(a):
    if a.json:
        print(json.dumps(U.snapshot(), indent=2, ensure_ascii=False))
    else:
        print(U.report())
    return 0


def cmd_status(a):
    import server
    print(server.t_org_status({}))
    print()
    print(f"dashboard : {url()}  ({'up' if dashboard_up() else 'down'})")
    if shutil.which("systemctl"):
        st = _systemctl("is-enabled", SERVICE).stdout.strip() or "not registered"
        print(f"service   : {_systemctl('is-active', SERVICE).stdout.strip()} / {st}")
    return 0


def _stdin_text():
    """Piped stdin, if any. Not read when -f is given: an inherited but idle stdin
    (cron, CI, a parent process) would otherwise block forever."""
    return None if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read().strip()


def cmd_run(a):
    import server
    data = None if a.files else _stdin_text()
    args = {"role": a.role, "task": a.task, "max_tokens": a.max_tokens}
    if data:
        args["input"] = data
    if a.model:
        args["model"] = a.model
    if a.output:
        args["output_file"] = os.path.abspath(os.path.expanduser(a.output))
    if a.no_cache:
        args["no_cache"] = True
    if a.files:
        args["files"] = _abs(a.files)
    print(server.invoke("delegate", args))
    return 0


def cmd_fanout(a):
    import server
    items = [] if a.files else [l.strip() for l in (_stdin_text() or "").splitlines() if l.strip()]
    if not items and not a.files:
        print("fanout expects task: each line in stdin is a task, or files with -f. Example:\n"
              "  ai-workers fanout summarizer 'Summarize the file' -f '~/code/myapp/docs/*.md'",
              file=sys.stderr)
        return 2
    args = {"role": a.role, "task": a.task, "items": items,
            "concurrency": a.concurrency, "max_tokens": a.max_tokens}
    if a.files:
        args["files"] = _abs(a.files)
    if a.output_dir:
        args["output_dir"] = os.path.abspath(os.path.expanduser(a.output_dir))
    if a.reduce:
        args["reduce"] = a.reduce
    if a.no_cache:
        args["no_cache"] = True
    print(server.invoke("fanout", args))
    return 0


def cmd_models(a):
    import server
    print(server.t_models({"provider": a.provider, "free_only": a.free_only}))
    return 0


def cmd_roles(a):
    roles = P.load_roles()
    for name, spec in roles.items():
        print(f"{name:12s} {spec['provider']}/{spec['model']}")
        if a.verbose:
            print(f"             {spec.get('system', '')[:150]}")
    return 0


def cmd_serve(a):
    import dashboard
    sys.argv = ["dashboard.py", "--port", str(a.port or port())]
    dashboard.main()
    return 0


def cmd_service(a):
    if not shutil.which("systemctl"):
        print("systemctl not found", file=sys.stderr)
        return 1
    if a.action == "logs":
        os.execvp("journalctl", ["journalctl", "--user", "-u", SERVICE, "-n", "60", "-f"])
    r = _systemctl(a.action, SERVICE)
    out = (r.stdout + r.stderr).strip()
    if out:
        print(out)
    if a.action in ("start", "restart"):
        print(f"dashboard: {url()}")
    return r.returncode


def _sent_effort(provider, model, effort):
    """reasoning_effort exactly as _build_payload would put it on the wire (or None)."""
    return P._build_payload(provider, model, [], 1, 0, effort).get("reasoning_effort")


def live_targets(roles=None):
    """Distinct (provider, model, effort-as-sent) the configuration can route to, in order."""
    roles = P.load_roles() if roles is None else roles
    out, seen = [], set()

    def add(prov, model, effort):
        eff = _sent_effort(prov, model, effort)
        if (prov, model, eff) not in seen:
            seen.add((prov, model, eff))
            out.append((prov, model, eff))

    for spec in roles.values():
        prov = spec["provider"]
        pcfg = P.PROVIDERS.get(prov) or {}
        eff = spec.get("reasoning_effort")
        prim = spec.get("model") or pcfg.get("default_model")
        add(prov, prim, eff)
        for m in spec.get("pool") or pcfg.get("pool") or []:
            add(prov, m, eff)
        fm = spec.get("fallback_models") or {}
        for q in P.FALLBACK_ORDER:
            if q != prov:
                add(q, fm.get(q) or P.PROVIDERS[q]["default_model"], eff)
    return out


def _live_call(prov, model, eff, lock):
    arg = {prov: eff} if eff else None
    t = time.time()
    try:
        if lock:
            lock.acquire()
        res = P.chat(prov, "Reply with the word OK.", model=model, max_tokens=64,
                     reasoning_effort=arg, retries=1, role="doctor")
    finally:
        if lock:
            lock.release()
    return res, time.time() - t


def doctor_live(no_openrouter=False):
    """Probe every routable model with the parameters roles send. Returns True if none failed."""
    targets = live_targets()
    have = P.available_providers()
    print("\n# live model check\n")
    n_or = sum(1 for p, _, _ in targets if p == "openrouter" and p in have)
    if no_openrouter:
        targets = [t for t in targets if t[0] != "openrouter"]
    elif n_or:
        print(f"note: live doctor uses {n_or} openrouter request(s) (~50/day free quota); "
              "skip with --no-openrouter\n")
    lock = threading.Lock()
    rows = [None] * len(targets)
    jobs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for i, (prov, model, eff) in enumerate(targets):
            if prov not in have:
                rows[i] = ("SKIP   ", "no key")
                continue
            cool = P._cooling(prov, model)
            if cool:
                rows[i] = ("COOLING", cool.replace("cooling down ", ""))
                continue
            jobs[ex.submit(_live_call, prov, model, eff, lock if prov == "openrouter" else None)] = i
        for f in concurrent.futures.as_completed(jobs):
            i = jobs[f]
            try:
                res, dt = f.result()
            except Exception as e:
                res, dt = {"ok": False, "error": f"{type(e).__name__}: {e}"}, 0.0
            if res.get("ok"):
                rows[i] = ("OK     ", f"{dt:.2f} s")
            else:
                rows[i] = ("FAIL   ", str(res.get("error"))[:120])
    ok, hints = True, []
    for (prov, model, eff), (mark, info) in zip(targets, rows):
        e = f" effort={eff}" if eff else ""
        sep = "  " if mark in ("OK     ", "FAIL   ") else " "
        print(f"[{mark}] {prov} {model}{e}{sep}{info}")
        if mark == "FAIL   ":
            ok = False
            low = info.lower()
            if "404" in info or "model_not_found" in low:
                hints.append(f"-> {prov} {model}: run: ai-workers models {prov}")
            elif "400" in info and eff:
                hints.append(f"-> {prov} {model}: effort '{eff}' may be unsupported for this model "
                             "(the server retries without it, but fix the role config)")
    if hints:
        print()
        print("\n".join(hints))
    return ok


def cmd_doctor(a):
    ok = True
    print("# ai-workers doctor\n")
    have = P.available_providers()
    for p in P.PROVIDERS:
        mark = "OK     " if p in have else "MISSING"
        print(f"[{mark}] key: {p}")
    if not have:
        ok = False
        print(f"       -> add key to {P.ENV_FILE} file")

    print(f"[{'OK     ' if os.path.exists(P.CONFIG_FILE) else 'MISSING'}] config: {P.CONFIG_FILE}")
    print(f"[{'OK     ' if os.path.exists(P.LEDGER) else '-      '}] ledger: {P.LEDGER}")
    size = files = 0
    for root, _, names in os.walk(T.TRACE_DIR):
        for name in names:
            files += 1
            size += os.path.getsize(os.path.join(root, name))
    st = T.SETTINGS
    print(f"[{'OK     ' if st['enabled'] else '-      '}] activity log: {T.TRACE_DIR} "
          f"({size / 1e6:.1f} MB, {files} files; keeps {st['retention_days']} d / {st['max_mb']} MB)")
    import app
    print(f"[{'OK     ' if app.available() else '-      '}] desktop app (GTK + WebKit)"
          + ("" if app.available() else f": {app.why_unavailable()} -> browser fallback"))

    up = dashboard_up()
    print(f"[{'OK     ' if up else '-      '}] dashboard: {url()}")
    if shutil.which("systemctl"):
        act = _systemctl("is-active", SERVICE).stdout.strip()
        en = _systemctl("is-enabled", SERVICE).stdout.strip()
        print(f"[{'OK     ' if act == 'active' else '-      '}] service: {act} / {en or 'not registered'}")

    # MCP registration
    try:
        with open(os.path.expanduser("~/.claude.json")) as f:
            cj = json.load(f)
        reg = "ai-workers" in (cj.get("mcpServers") or {})
    except Exception:
        reg = False
    print(f"[{'OK     ' if reg else 'MISSING'}] Claude Code MCP registration (user scope)")
    if not reg:
        ok = False
        print(f"       -> claude mcp add ai-workers --scope user -- python3 {HERE}/server.py")

    # live claude limit
    live = U.claude_live()
    if live.get("ok"):
        print(f"[OK     ] claude -p /usage: {len(live['limits'])} limits read")
    elif live.get("pending"):
        print("[-      ] claude -p /usage: taking first measurement")
    else:
        print(f"[MISSING] claude -p /usage: {live.get('error')}")

    if getattr(a, "live", False):
        ok = doctor_live(getattr(a, "no_openrouter", False)) and ok

    print("\n" + ("ready." if ok else "there are missing items (see above)."))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(prog="ai-workers",
                                 description="Worker pool consisting of free API models")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("app", help="open the desktop app (default)")
    p.add_argument("--foreground", action="store_true", help="do not detach from the terminal")
    p.add_argument("--inspect", action="store_true", help="enable the web inspector")
    p.set_defaults(fn=cmd_app)

    p = sub.add_parser("activity", aliases=["trace"],
                       help="recent tool calls, jobs and provider requests")
    p.add_argument("-f", "--follow", action="store_true", help="keep printing new activity")
    p.add_argument("-n", type=int, default=20, help="number of recent calls (default 20)")
    p.set_defaults(fn=cmd_activity)

    p = sub.add_parser("open", help="open the dashboard in the browser")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(fn=cmd_open)

    p = sub.add_parser("usage", help="remaining usage report")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_usage)

    p = sub.add_parser("status", help="provider and role status")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("run", help="delegate a single task to a role")
    p.add_argument("role"); p.add_argument("task")
    p.add_argument("--model"); p.add_argument("--max-tokens", type=int, default=None,
                                              dest="max_tokens")
    p.add_argument("-f", "--files", nargs="+", help="file/directory/glob (read by server)")
    p.add_argument("-o", "--output", help="write the answer to this file (prints path + preview)")
    p.add_argument("--no-cache", action="store_true", dest="no_cache", help="bypass the result cache")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("fanout", help="distribute each line in stdin as a parallel task")
    p.add_argument("role"); p.add_argument("task")
    p.add_argument("-c", "--concurrency", type=int, default=6)
    p.add_argument("--max-tokens", type=int, default=None, dest="max_tokens")
    p.add_argument("-f", "--files", nargs="+", help="each file is a separate task (split if large)")
    p.add_argument("--output-dir", dest="output_dir", help="write each job's answer to <dir>/NNN-label.md")
    p.add_argument("--reduce", help="instruction for a final job that merges all outputs")
    p.add_argument("--no-cache", action="store_true", dest="no_cache", help="bypass the result cache")
    p.set_defaults(fn=cmd_fanout)

    p = sub.add_parser("models", help="provider's live model list")
    p.add_argument("provider", choices=list(P.PROVIDERS))
    p.add_argument("--free-only", action="store_true", dest="free_only")
    p.set_defaults(fn=cmd_models)

    p = sub.add_parser("roles", help="defined roles")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_roles)

    p = sub.add_parser("serve", help="run dashboard in foreground")
    p.add_argument("--port", type=int)
    p.set_defaults(fn=cmd_serve)

    for act in ("start", "stop", "restart", "logs"):
        p = sub.add_parser(act, help=f"service: {act}")
        p.set_defaults(fn=cmd_service, action=act)

    p = sub.add_parser("doctor", help="installation check")
    p.add_argument("--live", action="store_true",
                   help="also probe every routable model with the parameters roles send (network)")
    p.add_argument("--no-openrouter", action="store_true",
                   help="with --live: skip openrouter (small daily free quota)")
    p.set_defaults(fn=cmd_doctor)

    T.configure(source="cli")
    a = ap.parse_args()
    if not a.cmd:
        a = ap.parse_args(["app"])
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
