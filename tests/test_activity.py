import _env  # noqa: F401  (must come first)
import json, os, subprocess, sys, textwrap, threading, time, unittest, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

import activity as A


def events():
    out = []
    for name in sorted(os.listdir(A.TRACE_DIR)):
        if name.startswith("events-"):
            with open(os.path.join(A.TRACE_DIR, name)) as f:
                out += [json.loads(line) for line in f]
    return out


class SpanTest(unittest.TestCase):
    def test_nesting_and_end_fields(self):
        with A.span("call", tool="delegate") as call:
            with A.span("job", label="j1") as job:
                with A.span("attempt", provider="groq") as att:
                    att.note("retry", attempt=1, delay=0.5)
                    att.set(status="ok", tokens={"in": 3, "out": 4})
            job.set(status="ok")
        evs = {(e["ev"], e.get("id")): e for e in events()}
        self.assertEqual(evs[("start", job.id)]["parent"], call.id)
        self.assertEqual(evs[("start", att.id)]["parent"], job.id)
        self.assertEqual(evs[("start", att.id)]["call"], call.id)
        self.assertEqual(evs[("end", att.id)]["tokens"], {"in": 3, "out": 4})
        self.assertEqual(evs[("note", att.id)]["name"], "retry")
        self.assertIn("dur", evs[("end", call.id)])

    def test_exception_marks_span_failed(self):
        with self.assertRaises(ValueError):
            with A.span("call", tool="x") as call:
                raise ValueError("boom")
        end = [e for e in events() if e["ev"] == "end" and e["id"] == call.id][0]
        self.assertEqual(end["status"], "error")
        self.assertIn("boom", end["error"])

    def test_bound_carries_parent_into_threads(self):
        def work(i):
            with A.span("job", label=str(i)) as sp:
                return sp
        with A.span("call", tool="fanout") as call:
            with ThreadPoolExecutor(4) as ex:
                spans = [f.result() for f in [ex.submit(A.bound(work), i) for i in range(8)]]
        self.assertTrue(all(sp.parent == call.id and sp.call == call.id for sp in spans))

    def test_session_logged_once_per_process(self):
        with A.span("call", tool="a"):
            pass
        with A.span("call", tool="b"):
            pass
        sessions = [e for e in events() if e["ev"] == "session" and e["sid"] == A.SESSION["sid"]]
        self.assertEqual(len(sessions), 1)


class BlobTest(unittest.TestCase):
    def test_small_payload_inline(self):
        self.assertEqual(A.blob("hi"), {"text": "hi", "size": 2})
        self.assertIsNone(A.blob(None))

    def test_large_payload_deduplicated(self):
        text = "x" * (A.INLINE_MAX + 10)
        b1, b2 = A.blob(text), A.blob(text)
        self.assertEqual(b1["ref"], b2["ref"])
        self.assertEqual(b1["size"], len(text))
        self.assertEqual(len(b1["preview"]), A.PREVIEW_CHARS)
        self.assertEqual(A.read_blob(b1["ref"]), text)

    def test_read_blob_rejects_paths(self):
        for bad in ("../events", "/etc/passwd", "a" * 39, "g" * 40, ""):
            self.assertIsNone(A.read_blob(bad))


class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.join(_env.HOME, f"reader-{time.time_ns()}")
        os.makedirs(self.dir)
        self.path = os.path.join(self.dir, "events-20260101.jsonl")

    def write(self, *evs, partial=""):
        with open(self.path, "a") as f:
            for e in evs:
                f.write(json.dumps(e) + "\n")
            f.write(partial)

    def test_tree_summary_and_partial_lines(self):
        r = A.Reader(self.dir)
        self.write({"ev": "session", "sid": "s1", "pid": os.getpid(), "source": "mcp", "cwd": "/x/proj"},
                   {"ev": "start", "id": "c", "call": "c", "parent": None, "kind": "call", "ts": 1, "sid": "s1", "tool": "fanout"},
                   {"ev": "start", "id": "j", "call": "c", "parent": "c", "kind": "job", "ts": 1, "role": "summarizer"},
                   {"ev": "start", "id": "a1", "call": "c", "parent": "j", "kind": "attempt", "ts": 1, "provider": "groq", "model": "m"},
                   partial='{"ev": "end", "id": "a1", "call": "c", "kind": "attempt", "ts": 2, "status": "er')
        self.assertEqual(r.refresh(), {"c"})
        s = r.list_calls()[0]
        self.assertEqual((s["status"], s["jobs"], s["attempts"], s["project"]), ("running", 1, 1, "proj"))
        self.assertEqual(s["active"], ["groq/m"])
        with open(self.path, "a") as f:  # finish the partial line, then complete the call
            f.write('ror", "error": "HTTP 429"}\n')
        self.write({"ev": "start", "id": "a2", "call": "c", "parent": "j", "kind": "attempt", "ts": 2, "provider": "gemini", "model": "g"},
                   {"ev": "end", "id": "a2", "call": "c", "kind": "attempt", "ts": 3, "status": "ok", "tokens": {"in": 10, "out": 5}},
                   {"ev": "end", "id": "j", "call": "c", "kind": "job", "ts": 3, "status": "ok"},
                   {"ev": "end", "id": "c", "call": "c", "kind": "call", "ts": 3, "status": "ok", "dur": 2})
        r.refresh()
        s = r.list_calls()[0]
        self.assertEqual(s["status"], "ok")
        self.assertEqual((s["failed_attempts"], s["skipped_attempts"]), (1, 0))
        self.assertEqual(s["providers"], ["gemini/g"])
        self.assertEqual(s["tokens"], {"in": 10, "out": 5})
        d = r.detail("c")
        self.assertEqual([a["status"] for a in d["children"][0]["children"]], ["error", "ok"])
        self.assertIsNone(r.detail("j"))  # only calls are addressable

    def test_dead_process_marks_running_call_abandoned(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
        self.write({"ev": "session", "sid": "s2", "pid": dead.pid, "source": "mcp"},
                   {"ev": "start", "id": "c2", "call": "c2", "parent": None, "kind": "call", "ts": 1, "sid": "s2", "tool": "delegate"})
        r = A.Reader(self.dir)
        r.refresh()
        self.assertEqual(r.list_calls()[0]["status"], "abandoned")


class PruneTest(unittest.TestCase):
    def setUp(self):  # own directories: other tests write to the shared trace dir
        self.saved = (A.TRACE_DIR, A.BLOB_DIR, dict(A.SETTINGS))
        A.TRACE_DIR = os.path.join(_env.HOME, f"prune-{time.time_ns()}")
        A.BLOB_DIR = os.path.join(A.TRACE_DIR, "blobs")
        os.makedirs(A.BLOB_DIR)

    def tearDown(self):
        A.TRACE_DIR, A.BLOB_DIR = self.saved[:2]
        A.SETTINGS.clear(); A.SETTINGS.update(self.saved[2])

    @staticmethod
    def make(path, size, mtime):
        with open(path, "w") as f:
            f.write("x" * size)
        os.utime(path, (mtime, mtime))
        return path

    def test_age_limit(self):
        now = time.time()
        old = self.make(os.path.join(A.TRACE_DIR, "events-20000101.jsonl"), 10, now - 30 * 86400)
        fresh = self.make(os.path.join(A.BLOB_DIR, "f" * 40), 10, now - 60)
        A.prune(now)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))

    def test_size_limit_removes_oldest_first(self):
        now = time.time()
        blobs = [self.make(os.path.join(A.BLOB_DIR, f"{i:040x}"), 1000, now - 100 + i) for i in range(3)]
        A.SETTINGS["max_mb"] = 0.0025  # 2,500 bytes: room for two of the three blobs
        A.prune(now)
        self.assertEqual([os.path.exists(p) for p in blobs], [False, True, True])


class ConcurrentWriteTest(unittest.TestCase):
    def test_many_processes_append_valid_lines(self):
        code = textwrap.dedent("""
            import sys; sys.path.insert(0, %r)
            import activity as A
            for i in range(150):
                with A.span("call", tool="t", payload="y" * 300):
                    pass
        """ % _env.ROOT)
        before = len(events())
        procs = [subprocess.Popen([sys.executable, "-c", code], env=_env.clean_env()) for _ in range(4)]
        for p in procs:
            self.assertEqual(p.wait(60), 0)
        evs = events()  # json.loads on every line: a torn write would raise here
        self.assertEqual(len(evs) - before, 4 * (150 * 2 + 1))


class ServerIntegrationTest(unittest.TestCase):
    def test_delegate_without_keys_is_fully_logged(self):
        import server
        text = server.invoke("delegate", {"role": "classifier", "task": "x", "input": "y"})
        self.assertTrue(text.startswith("WORKER FAILED"))
        r = A.Reader()
        r.refresh()
        s = r.list_calls(limit=1)[0]
        self.assertEqual((s["tool"], s["status"], s["jobs"]), ("delegate", "error", 1))
        d = r.detail(s["id"])
        atts = d["children"][0]["children"]
        self.assertEqual([a["status"] for a in atts], ["skipped"] * 3)
        self.assertEqual(d["children"][0]["notes"][0]["name"], "fallback")
        self.assertIn("WORKER FAILED", d["result"]["text"])


class DashboardApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import dashboard
        cls.srv = dashboard.make_server(0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        with A.span("call", tool="delegate", title="api test") as call:
            call.set(result=A.blob("z" * (A.INLINE_MAX + 1)))
        cls.call_id = call.id

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def get(self, path, host=None):
        req = urllib.request.Request(self.base + path, headers={"Host": host} if host else {})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers, r.read()

    def test_health(self):
        self.assertEqual(json.loads(self.get("/api/health")[2])["app"], "ai-workers")

    def test_foreign_host_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/activity", host="attacker.example")
        self.assertEqual(cm.exception.code, 403)

    def test_no_cors_headers(self):
        _, headers, _ = self.get("/api/activity")
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_activity_list_detail_and_blob(self):
        deadline = time.time() + 5
        while time.time() < deadline:
            calls = json.loads(self.get("/api/activity")[2])["calls"]
            if any(c["id"] == self.call_id for c in calls):
                break
            time.sleep(0.2)
        d = json.loads(self.get(f"/api/activity/{self.call_id}")[2])
        self.assertEqual(d["title"], "api test")
        body = self.get(f"/api/blob/{d['result']['ref']}")[2].decode()
        self.assertEqual(body, "z" * (A.INLINE_MAX + 1))
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/blob/" + "0" * 40)
        self.assertEqual(cm.exception.code, 404)

    def test_stream_says_hello(self):
        with urllib.request.urlopen(self.base + "/api/stream", timeout=5) as r:
            self.assertEqual(r.headers["Content-Type"], "text/event-stream")
            self.assertEqual(r.readline().decode().strip(), "event: hello")

    def test_page_served(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b'src="/ui/core.js"', body)

    def test_ui_assets_served(self):
        for name, ctype in (("core.js", "text/javascript"), ("apollo.css", "text/css"), ("i18n.js", "text/javascript")):
            status, headers, body = self.get("/ui/" + name)
            self.assertEqual(status, 200)
            self.assertTrue(headers["Content-Type"].startswith(ctype))

    def test_ui_paths_cannot_escape(self):
        for path in ("/ui/../dashboard.py", "/ui/%2e%2e/dashboard.py", "/ui/.hidden", "/ui/missing.js", "/ui/x.py"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(path)
            self.assertEqual(cm.exception.code, 404, path)


if __name__ == "__main__":
    unittest.main()
