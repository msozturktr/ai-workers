import _env  # noqa: F401  (must come first)
import os, tempfile, unittest
from unittest import mock

import providers as P
import server
from test_pack import _res

EXTRACTOR = {"provider": "groq", "model": "openai/gpt-oss-20b", "system": "sys",
             "max_tokens": 2048, "min_tokens": 512, "validate": "json"}
TRANSLATOR = {"provider": "groq", "model": "openai/gpt-oss-20b", "system": "sys",
              "max_tokens": 2048, "min_tokens": 512}
ROLES = {"extractor": EXTRACTOR, "translator": TRANSLATOR}


class IsNoneTest(unittest.TestCase):
    def test_variants(self):
        for t in ("NONE", " none. ", "**None**", "`none`", "```json\nNONE\n```", "```\nnone\n```"):
            self.assertTrue(P.is_none(t), t)
        for t in ("", None, "Not none here", "```json\n{\"a\": 1}\n```", "NONE of it"):
            self.assertFalse(P.is_none(t), t)


class NoneValidationTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(server, "_roles", return_value=ROLES)
        p.start()
        self.addCleanup(p.stop)

    def chat(self, answers):
        self.n = 0
        def fn(provider, prompt, **kw):
            a = answers[min(self.n, len(answers) - 1)]
            self.n += 1
            return _res(a)
        m = mock.patch.object(P, "chat", side_effect=fn)
        m.start()
        self.addCleanup(m.stop)

    def test_validate_normalises(self):
        for t in ("NONE", "```json\nNONE\n```"):
            res = {"text": t}
            self.assertIsNone(P._validate(EXTRACTOR, res))
            self.assertEqual(res["text"], "NONE")

    def test_no_fallback_no_warning(self):
        for ans in ("NONE", "```json\nNONE\n```"):
            self.chat([ans])
            res = P.run(EXTRACTOR, "### FILE: a.cs\nx", no_cache=True, role="extractor")
            self.assertTrue(res["ok"])
            self.assertEqual(res["text"], "NONE")
            self.assertEqual(self.n, 1)
            self.assertFalse(res.get("warning_note"))
            self.assertEqual(res["route"], [])

    def test_drop_none_omits_fenced(self):
        self.chat(["```json\nNONE\n```"])
        out = server.t_fanout({"role": "extractor", "task": "scan", "items": ["a", "b"],
                               "concurrency": 1, "drop_none": True, "pack": False, "no_cache": True})
        self.assertIn("0 with results (2 NONE omitted)", out)
        self.assertNotIn("WARNING", out)


class HeaderEchoTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(server, "_roles", return_value=ROLES)
        p.start()
        self.addCleanup(p.stop)

    def chat(self, answer):
        m = mock.patch.object(P, "chat", side_effect=lambda *a, **k: _res(answer))
        m.start()
        self.addCleanup(m.stop)

    def test_strip_unit(self):
        pr = "task\n\n### FILE: howitworks.md\nbody"
        self.assertEqual(P.strip_echoed_header("### FILE: howitworks.md\nçeviri", pr), "çeviri")
        self.assertEqual(P.strip_echoed_header("### FILE: other.md\nx", pr), "### FILE: other.md\nx")
        self.assertEqual(P.strip_echoed_header("### Notes\nx", pr), "### Notes\nx")
        self.assertEqual(P.strip_echoed_header("hi\n### FILE: howitworks.md\nx", pr),
                         "hi\n### FILE: howitworks.md\nx")

    def test_delegate_and_output_file(self):
        self.chat("### FILE: howitworks.md\nmerhaba")
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "howitworks.md")
            open(src, "w").write("hello")
            out = server.t_delegate({"role": "translator", "task": "tr", "files": [src],
                                     "no_cache": True})
            self.assertNotIn("### FILE:", out)
            self.assertIn("merhaba", out)
            of = os.path.join(d, "out.md")
            server.t_delegate({"role": "translator", "task": "tr", "files": [src],
                               "no_cache": True, "output_file": of})
            self.assertEqual(open(of).read().strip(), "merhaba")

    def test_other_heading_kept(self):
        self.chat("### Summary\nmerhaba")
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "howitworks.md")
            open(src, "w").write("hello")
            out = server.t_delegate({"role": "translator", "task": "tr", "files": [src],
                                     "no_cache": True})
            self.assertIn("### Summary\nmerhaba", out)


if __name__ == "__main__":
    unittest.main()
