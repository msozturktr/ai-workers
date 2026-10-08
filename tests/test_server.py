"""MCP protocol tests: drive server.py over stdio like a real client. No network."""
import _env  # noqa: F401  (must come first)
import json, os, subprocess, sys, threading, time, unittest

SERVER = os.path.join(_env.ROOT, "server.py")


class Client:
    def __init__(self, env):
        self.p = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, env=env)
        self.resp, self.cv, self.nid = {}, threading.Condition(), 0
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.p.stdout:
            m = json.loads(line)
            with self.cv:
                self.resp[m.get("id")] = m
                self.cv.notify_all()

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.nid += 1
            msg["id"] = self.nid
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        return msg.get("id")

    def request(self, method, params=None, timeout=30):
        mid = self.send(method, params)
        with self.cv:
            self.cv.wait_for(lambda: mid in self.resp, timeout)
        return self.resp.get(mid)

    def call(self, name, args, timeout=30):
        m = self.request("tools/call", {"name": name, "arguments": args}, timeout)
        return m["result"]["content"][0]["text"]

    def close(self):
        self.p.stdin.close()
        self.p.wait(30)
        self.p.stdout.close()


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = Client(_env.clean_env())
        cls.init = cls.c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                "clientInfo": {"name": "test", "version": "1"}})
        cls.c.send("notifications/initialized", notify=True)

    @classmethod
    def tearDownClass(cls):
        cls.c.close()

    def test_initialize_sends_instructions(self):
        r = self.init["result"]
        self.assertEqual(r["serverInfo"]["name"], "ai-workers")
        self.assertIn("files", r["instructions"])

    def test_delegate_and_fanout_always_loaded(self):
        tools = self.c.request("tools/list")["result"]["tools"]
        meta = {t["name"]: t.get("_meta") or {} for t in tools}
        self.assertTrue(meta["delegate"].get("anthropic/alwaysLoad"))
        self.assertTrue(meta["fanout"].get("anthropic/alwaysLoad"))
        self.assertFalse(meta["ask"].get("anthropic/alwaysLoad"))
        props = {t["name"]: t["inputSchema"]["properties"] for t in tools}
        self.assertIn("files", props["delegate"])
        self.assertIn("whole_files", props["fanout"])

    def test_ping(self):
        self.assertEqual(self.c.request("ping")["result"], {})

    def test_unknown_tool_is_jsonrpc_error(self):
        m = self.c.request("tools/call", {"name": "nope", "arguments": {}})
        self.assertEqual(m["error"]["code"], -32602)

    def test_unknown_method_is_jsonrpc_error(self):
        self.assertEqual(self.c.request("nope/nope")["error"]["code"], -32601)

    def test_org_status_without_keys(self):
        t = self.c.call("org_status", {})
        self.assertIn("NO KEY", t)
        self.assertIn("summarizer", t)

    def test_delegate_rejects_relative_path(self):
        t = self.c.call("delegate", {"role": "summarizer", "task": "x", "files": ["rel/a.md"]})
        self.assertIn("absolute path required", t)

    def test_delegate_never_sends_secrets(self):
        os.makedirs(os.path.join(_env.HOME, ".config", "ai-workers"), exist_ok=True)
        env_file = os.path.join(_env.HOME, ".config", "ai-workers", "env")
        open(env_file, "w").close()
        t = self.c.call("delegate", {"role": "summarizer", "task": "x", "files": [env_file]})
        self.assertIn("no readable files", t)
        self.assertIn("secret file", t)

    def test_delegate_unknown_role(self):
        t = self.c.call("delegate", {"role": "nope", "task": "x"})
        self.assertIn("role 'nope' does not exist", t)

    def test_delegate_without_keys_fails_cleanly(self):
        t = self.c.call("delegate", {"role": "classifier", "task": "x", "input": "y"})
        self.assertTrue(t.startswith("WORKER FAILED"), t[:200])
        self.assertNotIn("SERVER ERROR", t)

    def test_fanout_without_jobs(self):
        t = self.c.call("fanout", {"role": "summarizer", "task": "x", "files": ["/nonexistent/*.cs"]})
        self.assertIn("no jobs", t)

    def test_fanout_job_limit(self):
        t = self.c.call("fanout", {"role": "classifier", "task": "x", "items": ["a"] * 201})
        self.assertIn("> 200", t)

    def test_fanout_failures_are_per_job(self):
        t = self.c.call("fanout", {"role": "classifier", "task": "x", "items": ["a", "b"]})
        self.assertTrue(t.startswith("# fanout: 0/2"), t[:200])
        self.assertEqual(t.count("[FAILED]"), 2)


@unittest.skipUnless(os.environ.get("AI_WORKERS_LIVE") or _env.ORIG_ENV.get("AI_WORKERS_LIVE"),
                     "set AI_WORKERS_LIVE=1 to run against real providers (uses quota)")
class LiveTest(unittest.TestCase):
    """End-to-end against real APIs with the user's real config and keys."""

    @classmethod
    def setUpClass(cls):
        cls.c = Client(_env.ORIG_ENV)
        cls.c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "live", "version": "1"}})

    @classmethod
    def tearDownClass(cls):
        cls.c.close()

    def test_delegate_small_file(self):
        t = self.c.call("delegate", {"role": "extractor", "max_tokens": 1500,
                                     "task": "List the top-level function names in this file as a JSON array.",
                                     "files": [os.path.join(_env.ROOT, "sources.py")]}, timeout=300)
        self.assertNotIn("FAILED", t.splitlines()[0])
        for name in ("resolve", "chunk", "bundle"):
            self.assertIn(name, t)

    def test_fanout_does_not_block_ping(self):
        mid = self.c.send("tools/call", {"name": "fanout", "arguments": {
            "role": "classifier", "task": "Answer with one word: positive or negative.",
            "items": ["Great product.", "Terrible service."], "max_tokens": 600}})
        t0 = time.time()
        self.c.request("ping", timeout=10)
        self.assertLess(time.time() - t0, 1.0)
        with self.c.cv:
            self.c.cv.wait_for(lambda: mid in self.c.resp, 300)
        text = self.c.resp[mid]["result"]["content"][0]["text"]
        self.assertTrue(text.startswith("# fanout: 2/2"), text[:200])


if __name__ == "__main__":
    unittest.main()
