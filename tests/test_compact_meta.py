import _env  # noqa: F401  (must come first)
import os, shutil, tempfile, unittest
from unittest import mock

import providers as P
import server


def _spec():
    return {"provider": "groq", "model": "openai/gpt-oss-20b", "system": "s",
            "max_tokens": 2048, "min_tokens": 512}


ROLES = {r: _spec() for r in ("classifier", "summarizer")}


def _res(text="ok", **kw):
    r = {"ok": True, "provider": "groq", "model": "openai/gpt-oss-20b", "text": text,
         "tokens": {"in": 1, "out": 1}}
    r.update(kw)
    return r


class ShortNameTest(unittest.TestCase):
    def test_short(self):
        s = server._short
        self.assertEqual(s("openai/gpt-oss-120b"), "gpt-oss-120b")
        self.assertEqual(s("nvidia/nemotron-3-ultra-550b-a55b:free"), "nemotron-3-ultra-550b-a55b")
        self.assertEqual(s("meta-llama/llama-4/x:free"), "x")
        self.assertEqual(s("gemini-3.6-flash"), "gemini-3.6-flash")

    def test_fb_summary_uses_short_names(self):
        out = server._fb_summary([{"provider": "groq", "model": "openai/gpt-oss-120b", "error": "429"}])
        self.assertEqual(out, "groq/gpt-oss-120b: 429")


class FanoutTagTest(unittest.TestCase):
    def setUp(self):
        m = mock.patch.object(server, "_roles", return_value=ROLES)
        m.start()
        self.addCleanup(m.stop)

    def fanout(self, fn, **kw):
        with mock.patch.object(P, "run", side_effect=fn):
            return server.t_fanout({"role": "summarizer", "task": "t", "concurrency": 1,
                                    "pack": False, **kw})

    def test_dominant_untagged_flags_shown_normal(self):
        def fn(spec, prompt, **kw):
            if prompt.endswith("c"):
                return _res("l1\nl2", cached=True)
            if prompt.endswith("f"):
                return _res("l1\nl2", model="google/gemini-3.6-flash", provider="gemini",
                            route=[{"provider": "groq", "model": "x", "error": "429"}])
            return _res("l1\nl2")
        out = self.fanout(fn, items=["a", "b", "c", "f"])
        self.assertIn("(concurrency=1) · summarizer -> groq/gpt-oss-20b", out.splitlines()[0])
        self.assertIn("\n## #1\n", out)
        self.assertIn("\n## #2\n", out)
        self.assertIn("## #3 [gpt-oss-20b cached]", out)
        self.assertIn("## #4 [gemini-3.6-flash fb<-groq]", out)

    def test_role_shown_when_differs(self):
        def fn(spec, prompt, **kw):
            return _res("l1\nl2")
        out = self.fanout(fn, items=["a", "b", {"input": "c", "role": "classifier", "label": "z"}])
        self.assertIn("## z [classifier: gpt-oss-20b]", out)

    def test_compact_flags(self):
        def fn(spec, prompt, **kw):
            return _res("S", cached=True) if prompt.endswith("c") else _res("S")
        out = self.fanout(fn, items=["a", "b", "c"]).splitlines()
        self.assertEqual(out[1], "- #1: S")
        self.assertEqual(out[3], "- #3: S [gpt-oss-20b cached]")

    def test_output_dir_tags(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        def fn(spec, prompt, **kw):
            return _res("S", cached=True) if prompt.endswith("c") else _res("S")
        out = self.fanout(fn, items=["a", "b", "c"], output_dir=os.path.join(d, "o"))
        self.assertIn("- #1 -> 001-1.md (1 chars)", out)
        self.assertIn("- #3 [gpt-oss-20b cached] -> 003-3.md (1 chars)", out)


class FailureTextTest(unittest.TestCase):
    def test_failed_text_compact(self):
        res = {"ok": False, "error": "all providers failed",
               "route": [{"provider": "groq", "model": "openai/a", "error": "429"},
                         {"provider": "groq", "model": "openai/b", "error": "429"},
                         {"provider": "gemini", "model": "g/c:free", "error": "missing key"}]}
        t = server._failed_text("WORKER FAILED", res)
        self.assertTrue(t.startswith("WORKER FAILED: all providers failed\n"))
        self.assertIn("- groq (2 models): 429", t)
        self.assertIn("- gemini/c: missing key", t)
        self.assertNotIn("{", t)
        self.assertLessEqual(len(server._failed_text("X", {"error": "e" * 2000})), 600)

    def test_status_of_new_failure(self):
        self.assertEqual(server._status_of("WORKER FAILED: all providers failed\n- groq/a: 429"), "error")
        self.assertEqual(server._status_of("FAILED: groq/x: boom"), "error")

    def test_ask_failure_compact(self):
        with mock.patch.object(P, "chat", return_value={"ok": False, "error": "e" * 500}):
            t = server.t_ask({"provider": "groq", "prompt": "p", "model": "m"})
        self.assertTrue(t.startswith("FAILED: groq/m: eee"))
        self.assertLessEqual(len(t), 320)

    def test_truncated_note(self):
        self.assertEqual(server._TRUNC_NOTE, "\n\n[TRUNCATED at max_tokens — incomplete; raise max_tokens]")


if __name__ == "__main__":
    unittest.main()
