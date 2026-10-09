#!/usr/bin/env python3
"""ai-workers dashboard server: quota overview + live activity log. Local only.

  python3 dashboard.py [--port 8765] [--once]

Endpoints
  GET /                     the UI (ui/index.html)
  GET /ui/<file>            UI assets (scripts, stylesheets)
  GET /api/health           liveness probe
  GET /api/usage            remaining-quota snapshot
  GET /api/activity         recent calls (summaries) + currently running count
  GET /api/activity/<id>    one call as a tree: call -> jobs -> attempts, with notes
  GET /api/blob/<sha1>      full payload (prompt, file contents, response)
  GET /api/stream           Server-Sent Events: "activity" whenever new events arrive

The activity log holds prompts and file contents, so requests must carry a loopback Host
header (blocks DNS rebinding) and no CORS headers are ever sent (blocks cross-origin reads).
"""
import argparse, json, os, re, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import activity
import usage

HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(HERE, "ui")
PAGE = os.path.join(UI_DIR, "index.html")
UI_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
VERSION = "1.6.2"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


class Activity:
    """One shared reader, refreshed by a background thread; SSE clients wait on `cond`."""

    def __init__(self):
        self.reader = activity.Reader()
        self.cond = threading.Condition()
        self.reader.refresh()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            time.sleep(0.4)
            try:
                changed = self.reader.refresh()
            except Exception:
                continue
            if changed:
                with self.cond:
                    self.cond.notify_all()


ACT = None
_STORAGE = {"ts": 0, "bytes": None}


def _storage_bytes():
    """Disk used by the activity log (cached for 30 s)."""
    if time.time() - _STORAGE["ts"] > 30:
        total = 0
        for base, _, names in os.walk(activity.TRACE_DIR):
            for name in names:
                try:
                    total += os.path.getsize(os.path.join(base, name))
                except OSError:
                    pass
        _STORAGE.update(ts=time.time(), bytes=total)
    return _STORAGE["bytes"]


_ACT_LOCK = threading.Lock()


def _activity():
    global ACT
    if ACT is None:
        with _ACT_LOCK:
            if ACT is None:
                ACT = Activity()
    return ACT


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass  # do not clutter the terminal

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        return name in ALLOWED_HOSTS

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "forbidden host", "text/plain; charset=utf-8")
        path = self.path.split("?")[0]
        query = dict(p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)
        try:
            if path == "/api/health":
                return self._json({"app": "ai-workers", "version": VERSION})
            if path == "/api/usage":
                try:
                    return self._json(usage.snapshot())
                except Exception as e:
                    return self._json({"error": f"{type(e).__name__}: {e}"})
            if path == "/api/activity":
                act = _activity()
                limit = max(1, min(int(query.get("limit", 300)), 2000))
                calls = act.reader.list_calls(limit=limit)
                return self._json({"version": act.reader.version, "calls": calls,
                                   "storage_bytes": _storage_bytes(),
                                   "running": sum(1 for c in calls if c["status"] == "running"),
                                   "settings": activity.SETTINGS, "dir": activity.TRACE_DIR})
            if path.startswith("/api/activity/"):
                d = _activity().reader.detail(path.rsplit("/", 1)[1])
                return self._json(d) if d else self._json({"error": "not found"}, 404)
            if path.startswith("/api/blob/"):
                text = activity.read_blob(path.rsplit("/", 1)[1])
                if text is None:
                    return self._send(404, "payload not found (pruned by retention?)",
                                      "text/plain; charset=utf-8")
                return self._send(200, text, "text/plain; charset=utf-8")
            if path == "/api/stream":
                return self._stream()
            if path in ("/", "/index.html"):
                return self._asset(PAGE)
            if path.startswith("/ui/"):
                name = path[4:]
                if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name) or ".." in name:
                    return self._send(404, "not found", "text/plain; charset=utf-8")
                return self._asset(os.path.join(UI_DIR, name))
            if path == "/icon.svg":
                with open(os.path.join(HERE, "icon.svg"), "rb") as f:
                    return self._send(200, f.read(), "image/svg+xml")
            self._send(404, "not found", "text/plain; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _asset(self, path):
        ctype = UI_TYPES.get(os.path.splitext(path)[1])
        if not ctype or not os.path.isfile(path):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        with open(path, "rb") as f:
            return self._send(200, f.read(), ctype)

    def _stream(self):
        act = _activity()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        seen = act.reader.version
        self.wfile.write(f"event: hello\ndata: {json.dumps({'version': seen})}\n\n".encode())
        self.wfile.flush()
        while True:
            with act.cond:
                if act.reader.version == seen:
                    act.cond.wait(timeout=15)
                version = act.reader.version
            if version != seen:
                seen = version
                msg = json.dumps({"version": version})
                self.wfile.write(f"event: activity\ndata: {msg}\n\n".encode())
            else:
                self.wfile.write(b": keep-alive\n\n")
            self.wfile.flush()


class Server(ThreadingHTTPServer):
    daemon_threads = True  # SSE connections must not block shutdown


def make_server(port):
    return Server(("127.0.0.1", port), Handler)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--once", action="store_true", help="print JSON without starting the server")
    a = ap.parse_args()
    if a.once:
        print(json.dumps(usage.snapshot(), indent=2, ensure_ascii=False))
        return
    srv = make_server(a.port)
    print(f"ai-workers dashboard -> http://127.0.0.1:{a.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
