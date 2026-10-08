#!/usr/bin/env python3
"""ai-workers command line application.

  ai-workers                 open dashboard (starts service if needed)
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
import argparse, json, os, shutil, subprocess, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import providers as P
import usage as U

SERVICE = "ai-workers.service"
DEFAULT_PORT = 8765


def port():
    try:
        cfg = json.load(open(P.CONFIG_FILE))
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
        with urllib.request.urlopen(url() + "/api/usage", timeout=3):
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
    if a.files:
        args["files"] = _abs(a.files)
    print(server.t_delegate(args))
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
    print(server.t_fanout(args))
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

    up = dashboard_up()
    print(f"[{'OK     ' if up else '-      '}] dashboard: {url()}")
    if shutil.which("systemctl"):
        act = _systemctl("is-active", SERVICE).stdout.strip()
        en = _systemctl("is-enabled", SERVICE).stdout.strip()
        print(f"[{'OK     ' if act == 'active' else '-      '}] service: {act} / {en or 'not registered'}")

    # MCP registration
    try:
        cj = json.load(open(os.path.expanduser("~/.claude.json")))
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

    print("\n" + ("ready." if ok else "there are missing items (see above)."))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(prog="ai-workers",
                                 description="Worker pool consisting of free API models")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("open", help="open dashboard in browser (default)")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(fn=cmd_open)

    p = sub.add_parser("usage", help="remaining usage report")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_usage)

    p = sub.add_parser("status", help="provider and role status")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("run", help="delegate a single task to a role")
    p.add_argument("role"); p.add_argument("task")
    p.add_argument("--model"); p.add_argument("--max-tokens", type=int, default=4096,
                                              dest="max_tokens")
    p.add_argument("-f", "--files", nargs="+", help="file/directory/glob (read by server)")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("fanout", help="distribute each line in stdin as a parallel task")
    p.add_argument("role"); p.add_argument("task")
    p.add_argument("-c", "--concurrency", type=int, default=4)
    p.add_argument("--max-tokens", type=int, default=4096, dest="max_tokens")
    p.add_argument("-f", "--files", nargs="+", help="each file is a separate task (split if large)")
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
    p.set_defaults(fn=cmd_doctor)

    a = ap.parse_args()
    if not a.cmd:
        a = ap.parse_args(["open"])
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
