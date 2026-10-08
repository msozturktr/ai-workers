#!/usr/bin/env python3
"""Kalan kullanim panosu. Yerel, sadece 127.0.0.1'e baglanir.

  python3 dashboard.py [--port 8765] [--once]
"""
import argparse, json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usage

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "dashboard.html")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # terminali kirletme

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/usage":
            try:
                body = json.dumps(usage.snapshot(), ensure_ascii=False).encode()
            except Exception as e:
                body = json.dumps({"error": f"{type(e).__name__}: {e}"}).encode()
            return self._send(200, body, "application/json; charset=utf-8")
        if path in ("/", "/index.html"):
            try:
                with open(PAGE, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                return self._send(500, b"dashboard.html bulunamadi", "text/plain")
        self._send(404, b"yok", "text/plain; charset=utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--once", action="store_true", help="sunucu acmadan JSON bas")
    a = ap.parse_args()
    if a.once:
        print(json.dumps(usage.snapshot(), indent=2, ensure_ascii=False))
        return
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"ai-workers panosu -> http://127.0.0.1:{a.port}  (Ctrl+C ile kapat)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nkapatildi")


if __name__ == "__main__":
    main()
