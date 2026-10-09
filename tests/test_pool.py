import _env  # noqa: F401  (must come first)
import http.client, io, json, os, tempfile, time, unittest, urllib.error
from unittest import mock

import cache as C
import providers as P
import usage as U


class FakeResp:
    def __init__(self, status=200, body=b'{"ok": 1}', headers=None, will_close=False):
        self.status, self.reason, self._body, self.will_close = status, "R", body, will_close
        self.msg = http.client.HTTPMessage()
        for k, v in (headers or {}).items():
            self.msg[k] = v

    def read(self):
        return self._body


class FakeConn:
    def __init__(self, script):
        self.script, self.closed, self.calls, self.sock, self.timeout = script, False, 0, None, None

    def request(self, *a, **k):
        self.calls += 1

    def getresponse(self):
        r = self.script.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def close(self):
        self.closed = True


class PoolTest(unittest.TestCase):
    def setUp(self):
        P._pool.clear()
        os.environ.pop("AI_WORKERS_NO_KEEPALIVE", None)

    def post(self, conns):
        it = iter(conns)
        return mock.patch.object(P, "_conn_factory", lambda *a: next(it))

    def test_reuse(self):
        c = FakeConn([FakeResp(), FakeResp()])
        with self.post([c]):
            P._post("https://h.example/v1/x", "k", {}, 5)
            data, h = P._post("https://h.example/v1/x", "k", {}, 5)
        self.assertEqual(data, {"ok": 1})
        self.assertEqual(c.calls, 2)
        self.assertFalse(c.closed)

    def test_retry_once_on_stale(self):
        stale = FakeConn([http.client.RemoteDisconnected("x")])
        fresh = FakeConn([FakeResp(), FakeResp()])
        P._pool_put(("https", "h.example", 443), stale)
        with self.post([fresh]):
            data, _ = P._post("https://h.example/v1/x", "k", {}, 5)
        self.assertEqual(data, {"ok": 1})
        self.assertTrue(stale.closed)

    def test_fresh_failure_propagates(self):
        c = FakeConn([http.client.RemoteDisconnected("x")])
        with self.post([c]):
            with self.assertRaises(http.client.RemoteDisconnected):
                P._post("https://h.example/v1/x", "k", {}, 5)

    def test_http_error(self):
        c = FakeConn([FakeResp(429, b'{"e": "slow"}', {"Retry-After": "3"})])
        with self.post([c]):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                P._post("https://h.example/v1/x", "k", {}, 5)
        e = cm.exception
        self.assertEqual(e.code, 429)
        self.assertEqual(e.headers.get("retry-after"), "3")
        self.assertEqual(json.loads(e.read()), {"e": "slow"})

    def test_connection_close_not_pooled(self):
        c = FakeConn([FakeResp(headers={"Connection": "close"})])
        with self.post([c]):
            P._post("https://h.example/v1/x", "k", {}, 5)
        self.assertTrue(c.closed)
        self.assertFalse(P._pool.get(("https", "h.example", 443)))

    def test_idle_cap(self):
        for _ in range(P._POOL_MAX_IDLE + 2):
            P._pool_put(("https", "h", 443), FakeConn([]))
        self.assertEqual(len(P._pool[("https", "h", 443)]), P._POOL_MAX_IDLE)

    def test_env_escape_hatch(self):
        with mock.patch.dict(os.environ, {"AI_WORKERS_NO_KEEPALIVE": "1"}), \
                mock.patch.object(P, "_post_urllib", return_value=({"u": 1}, {})) as m:
            self.assertEqual(P._post("https://h.example/", "k", {}, 5)[0], {"u": 1})
        m.assert_called_once()


class CachedLedgerTest(unittest.TestCase):
    def test_cached_row_and_usage(self):
        with tempfile.TemporaryDirectory() as d:
            led = os.path.join(d, "ledger.jsonl")
            res = {"ok": True, "provider": "groq", "model": "m", "text": "hi",
                   "tokens": {"in": 1000, "out": 500}}
            with mock.patch.object(P, "LEDGER", led), mock.patch.object(P, "CONFIG_DIR", d), \
                    mock.patch.object(U.P, "LEDGER", led), \
                    mock.patch.object(C, "get", return_value=dict(res)):
                out = P.run({}, "p", role="r")
                self.assertTrue(out["cached"])
                P._record("groq", "m", "r", res, {})
                P._record("gemini", "g", "r", {"ok": True, "tokens": {"in": 1, "out": 1}}, {})
                rows = [json.loads(l) for l in open(led)]
                self.assertEqual(rows[0]["cached"], True)
                self.assertEqual((rows[0]["saved_in"], rows[0]["saved_out"]), (1000, 500))
                self.assertEqual(rows[0]["in"], 0)
                with mock.patch.object(U.P, "api_key", return_value="k"), \
                        mock.patch.object(U.P, "key_info", return_value={"ok": False}):
                    w = {x["provider"]: x for x in U.workers_usage()}
                self.assertEqual(w["groq"]["today_requests"], 1)
                e = U.efficiency(U._ledger())
                self.assertEqual((e["jobs"], e["free"], e["gemini"], e["cache_hits"],
                                  e["tokens_saved"]), (3, 1, 1, 1, 1500))
                txt = U.format_report({"workers": [], "claude": {},
                                       "efficiency": e})
                self.assertIn("3 jobs: 1 free (33%), 1 gemini, 1 cache hits (1.5K tokens saved)", txt)


if __name__ == "__main__":
    unittest.main()
