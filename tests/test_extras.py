import _env  # noqa: F401  (must come first)
import os, shutil, tempfile, unittest
from unittest import mock

import providers as P
import server
import sources as S


class SecretDirsSymlinkTest(unittest.TestCase):
    def test_symlinked_home_still_detects_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            real, link = os.path.join(tmp, "R"), os.path.join(tmp, "H")
            os.makedirs(os.path.join(real, ".ssh"))
            key = os.path.join(real, ".ssh", "key")
            open(key, "w").close()
            os.symlink(real, link)
            dirs = S._secret_dirs(link)
            self.assertIn(os.path.join(link, ".ssh"), dirs)
            self.assertIn(os.path.realpath(os.path.join(link, ".ssh")), dirs)
            with mock.patch.object(S, "SECRET_DIRS", dirs):
                self.assertTrue(S._is_secret(os.path.join(link, ".ssh", "key")))
                self.assertTrue(S._is_secret(os.path.join(link, ".ssh", "other.txt")))


class BuildPayloadTest(unittest.TestCase):
    def build(self, provider, model, effort="low"):
        return P._build_payload(provider, model, [], 100, 0.2, effort)

    def test_effort_only_for_groq_gpt_oss(self):
        self.assertEqual(self.build("groq", "openai/gpt-oss-120b")["reasoning_effort"], "low")
        self.assertNotIn("reasoning_effort", self.build("gemini", "gemini-3.6-flash"))
        self.assertNotIn("reasoning_effort", self.build("groq", "llama-3.3-70b"))
        self.assertNotIn("reasoning_effort", self.build("groq", "openai/gpt-oss-120b", None))


class OversizedResultTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        p = mock.patch.object(server, "RESULTS_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.dir, True)
        server.HANDLERS["_big"] = lambda a: "x" * 70_000
        self.addCleanup(server.HANDLERS.pop, "_big", None)

    def test_truncates_and_saves_full_text(self):
        out = server.invoke("_big", {})
        self.assertLess(len(out), 70_000)
        files = os.listdir(self.dir)
        self.assertEqual(len(files), 1)
        path = os.path.join(self.dir, files[0])
        self.assertIn(path, out)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "x" * 70_000)

    def test_keeps_at_most_50_files(self):
        for _ in range(51):
            server.invoke("_big", {})
        self.assertLessEqual(len(os.listdir(self.dir)), 50)


if __name__ == "__main__":
    unittest.main()


class FallbackSummaryTest(unittest.TestCase):
    def test_groups_same_provider_and_error(self):
        route = [{"provider": "groq", "model": m, "error": f"input too large for groq/{m}: ~8321 tokens > 7800"}
                 for m in ("a", "b", "c")] + [{"provider": "gemini", "model": "g", "error": "HTTP 500"}]
        self.assertEqual(server._fb_summary(route),
                         "groq (3 models): input too large: ~8321 tokens, gemini/g: HTTP 500")


class ThinkingEffortTest(unittest.TestCase):
    def build(self, provider, model, effort):
        return P._build_payload(provider, model, [], 100, 0.2, effort)

    def test_role_defaults_per_provider(self):
        eff = P.DEFAULT_ROLES["translator"]["reasoning_effort"]
        self.assertEqual(self.build("gemini", "gemini-3.6-flash", eff)["reasoning_effort"], "minimal")
        # summarizer keeps Gemini thinking (measured quality loss with "minimal")
        self.assertNotIn("reasoning_effort",
                         self.build("gemini", "gemini-3.6-flash", P.DEFAULT_ROLES["summarizer"]["reasoning_effort"]))
        self.assertEqual(self.build("groq", "openai/gpt-oss-120b", eff)["reasoning_effort"], "low")
        self.assertNotIn("reasoning_effort", self.build("groq", "qwen/qwen3.8-27b", eff))
        self.assertNotIn("reasoning_effort", self.build("gemini", "gemini-3.6-flash", "low"))
        coder = P.DEFAULT_ROLES["coder"]["reasoning_effort"]
        self.assertNotIn("reasoning_effort", self.build("groq", "openai/gpt-oss-120b", coder))
        self.assertEqual(self.build("gemini", "gemini-3.6-flash", coder)["reasoning_effort"], "low")

    def test_rejected_effort_retried_without_it(self):
        import io, urllib.error
        bodies = []

        def post(url, key, payload, timeout=180):
            bodies.append(dict(payload))
            if "reasoning_effort" in payload:
                raise urllib.error.HTTPError(url, 400, "bad", {}, io.BytesIO(b"invalid argument"))
            return ({"choices": [{"message": {"content": "positive"}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}}, {})
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}), \
                mock.patch.object(P, "_post", side_effect=post), mock.patch.object(P, "_record"):
            res = P.chat("gemini", "x", model="gemini-3.5-flash-lite", max_tokens=50,
                         reasoning_effort={"gemini": "none"})
        self.assertTrue(res["ok"])
        self.assertEqual([("reasoning_effort" in b) for b in bodies], [True, False])

    def test_think_tokens(self):
        self.assertEqual(P._think_tokens({"prompt_tokens": 10000, "completion_tokens": 164,
                                          "total_tokens": 14219}), 4055)
        self.assertEqual(P._think_tokens({"prompt_tokens": 10, "completion_tokens": 20,
                                          "total_tokens": 30}), 0)
        self.assertEqual(P._think_tokens({"prompt_tokens": 10, "completion_tokens": 200,
                                          "total_tokens": 210,
                                          "completion_tokens_details": {"reasoning_tokens": 150}}), 150)
        self.assertEqual(P._think_tokens({}), 0)

    def test_ledger_row_and_report(self):
        import json, usage as U
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(P, "LEDGER", os.path.join(d, "l.jsonl")), \
                mock.patch.object(P, "RATELIMIT_SNAPSHOT", os.path.join(d, "rl.json")), \
                mock.patch.object(P, "CONFIG_DIR", d):
            P._record("gemini", "g", "r", {"ok": True, "tokens": {"in": 5, "out": 2, "think": 3900}}, {})
            row = json.loads(open(P.LEDGER).readline())
            self.assertEqual(row["think"], 3900)
            e = U.efficiency([row])
            self.assertEqual(e["think_tokens"], 3900)
            txt = U.format_report({"workers": [], "claude": {}, "efficiency": e})
            self.assertIn("3.9K thinking", txt)
            e0 = U.efficiency([dict(row, think=0)])
            self.assertNotIn("thinking", U.format_report({"workers": [], "claude": {}, "efficiency": e0}))
            w = {"provider": "gemini", "ready": True, "today_requests": 1, "today_failed": 0,
                 "today_tokens_in": 5250, "today_tokens_out": 281, "today_tokens_think": 3900,
                 "source": "local counter", "quota": None}
            self.assertIn("5250+281 tokens (+3.9K thinking)",
                          U.format_report({"workers": [w], "claude": {}}))
            w["today_tokens_think"] = 0
            self.assertNotIn("thinking", U.format_report({"workers": [w], "claude": {}}))
