import _env  # noqa: F401  (must come first)
import json, re, unittest
from unittest import mock

import providers as P
import server

def spec(role):
    return {"provider": "groq", "model": "openai/gpt-oss-20b", "system": "sys",
            "max_tokens": 2048, "min_tokens": 512}

ROLES = {r: spec(r) for r in ("classifier", "extractor", "translator", "summarizer")}


def _res(text, **kw):
    r = {"ok": True, "provider": "groq", "model": "openai/gpt-oss-20b", "text": text,
         "tokens": {"in": 1, "out": 1}}
    r.update(kw)
    return r


def _items_of(prompt):
    return re.findall(r"### ITEM (\d+)\n(.*?)(?=\n\n### ITEM |\Z)", prompt, re.S)


class PackTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        for p in (mock.patch.object(server, "_roles", return_value=ROLES),):
            p.start()
            self.addCleanup(p.stop)

    def patch(self, pack_fn=None):
        def fn(spec_, prompt, **kw):
            self.calls.append(prompt)
            if "### ITEM " in prompt:
                if pack_fn:
                    return pack_fn(prompt)
                return _res(json.dumps({n: f"A-{t}" for n, t in _items_of(prompt)}))
            return _res(f"S-{prompt.rsplit(chr(10), 1)[-1]}")
        m = mock.patch.object(P, "run", side_effect=fn)
        m.start()
        self.addCleanup(m.stop)

    def fanout(self, **kw):
        return server.t_fanout({"role": "classifier", "task": "classify", "concurrency": 1, **kw})

    def packs(self):
        return [c for c in self.calls if "### ITEM " in c]

    def test_auto_pack_30_items(self):
        self.patch()
        items = [f"x{i}" for i in range(30)]
        out = self.fanout(items=items)
        self.assertEqual(len(self.packs()), 2)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("packed 30 items into 2 requests", out)
        lines = [l for l in out.splitlines() if l.startswith("- ")]
        self.assertEqual(lines[0], "- #1: A-x0")
        self.assertEqual(lines[29], "- #30: A-x29")
        self.assertIn(" · classifier -> groq/gpt-oss-20b", out.splitlines()[0])

    def test_not_triggered(self):
        self.patch()
        self.fanout(items=["a", "b", "c"])
        self.assertEqual(self.packs(), [])
        self.calls.clear()
        self.fanout(items=["x" * 1501] * 5)
        self.assertEqual(self.packs(), [])
        self.calls.clear()
        self.fanout(items=["a", "b", "c", {"input": "d", "task": "other"}])
        self.assertEqual(self.packs(), [])
        self.calls.clear()
        self.fanout(items=["a", "b", "c", {"input": "d", "model": "m"}])
        self.assertEqual(self.packs(), [])
        self.calls.clear()
        self.fanout(role="summarizer", items=["a"] * 6)
        self.assertEqual(self.packs(), [])
        self.calls.clear()
        self.fanout(items=["a"] * 6, pack=False)
        self.assertEqual(self.packs(), [])

    def test_force_on_summarizer(self):
        self.patch()
        out = self.fanout(role="summarizer", items=["a", "b"], pack=True)
        self.assertEqual(len(self.packs()), 1)
        self.assertIn("packed 2 items into 1 requests", out)

    def test_malformed_json_reruns_individually(self):
        self.patch(lambda p: _res("not json at all"))
        out = self.fanout(items=["a", "b", "c", "d"])
        self.assertEqual(len(self.packs()), 1)
        self.assertEqual(len(self.calls), 5)
        self.assertNotIn("packed", out)
        self.assertIn("- #3: S-c", out)

    def test_missing_keys_rerun_only_those(self):
        self.patch(lambda p: _res(json.dumps({"1": "one", "3": "three", "4": "four"})))
        out = self.fanout(items=["a", "b", "c", "d"])
        self.assertEqual(len(self.calls), 2)
        self.assertIn("- #1: one", out)
        self.assertIn("- #2: S-b", out)
        self.assertIn("packed 3 items into 1 requests", out)

    def test_fenced_json_and_dict_values(self):
        self.patch(lambda p: _res('```json\n' + json.dumps(
            {"1": {"k": "é"}, "2": ["x"], "3": "s", "4": 5}) + '\n```'))
        out = self.fanout(role="extractor", items=["a", "b", "c", "d"])
        self.assertEqual(len(self.calls), 1)
        self.assertIn('- #1: {"k": "é"}', out)
        self.assertIn('- #2: ["x"]', out)
        self.assertIn("- #4: 5", out)

    def test_truncated_pack_reruns(self):
        self.patch(lambda p: _res('{"1": "a"}', truncated=True))
        self.fanout(items=["a", "b", "c", "d"])
        self.assertEqual(len(self.calls), 5)

    def test_compact_suffix_and_failures(self):
        def fn(spec_, prompt, **kw):
            if prompt.endswith("BAD"):
                return {"ok": False, "error": "boom", "route": []}
            if prompt.endswith("cc"):
                return _res("S", model="other")
            return _res("S")
        with mock.patch.object(P, "run", side_effect=fn):
            out = self.fanout(items=["a", "b", "cc", "BAD"], pack=False)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("# fanout: 3/4 successful"))
        self.assertEqual(lines[1], "- #1: S")
        self.assertEqual(lines[3], "- #3: S [other]")
        self.assertEqual(lines[4], "- #4: FAILED: boom")

    def test_long_or_multiline_keeps_old_format(self):
        with mock.patch.object(P, "run", return_value=_res("line1\nline2")):
            out = self.fanout(items=["a"], pack=False)
        self.assertIn("## #1\nline1\nline2", out)
        with mock.patch.object(P, "run", return_value=_res("x" * 201)):
            out = self.fanout(items=["a"], pack=False)
        self.assertIn("## #1\n", out)


if __name__ == "__main__":
    unittest.main()
