import _env  # noqa: F401  (must come first)
import json, os, tempfile, unittest
from unittest import mock

import providers as P
import server
from test_pack import ROLES, _res, _items_of

SUFFIX = "reply with exactly NONE."


class DropNoneTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        p = mock.patch.object(server, "_roles", return_value=ROLES)
        p.start()
        self.addCleanup(p.stop)

    def patch(self, answer):
        """answer(item_text) -> str for both packed and single prompts."""
        def fn(spec_, prompt, **kw):
            self.calls.append(prompt)
            if "### ITEM " in prompt:
                return _res(json.dumps({n: answer(t) for n, t in _items_of(prompt)}))
            return _res(answer(prompt.rsplit("\n", 1)[-1]))
        m = mock.patch.object(P, "run", side_effect=fn)
        m.start()
        self.addCleanup(m.stop)

    def fanout(self, **kw):
        return server.t_fanout({"role": "classifier", "task": "scan", "concurrency": 1,
                                "drop_none": True, "pack": False, **kw})

    def test_variants_omitted_and_header(self):
        ans = {"a": "NONE", "b": "none.", "c": "**NONE**", "d": "`None`", "e": "hit-e", "f": "Not none here"}
        self.patch(lambda t: ans[t])
        out = self.fanout(items=list(ans))
        self.assertTrue(out.startswith("# fanout: 6/6 successful, 2 with results (4 NONE omitted)"))
        self.assertIn("- #5: hit-e", out)
        self.assertIn("- #6: Not none here", out)
        self.assertNotIn("#1", out)
        self.assertTrue(all(SUFFIX in c for c in self.calls))

    def test_all_none(self):
        self.patch(lambda t: "NONE")
        out = self.fanout(items=["a", "b", "c"])
        self.assertEqual(out, "# fanout: 3/3 successful, 0 with results (3 NONE omitted)")

    def test_reduce_skipped_when_no_results(self):
        self.patch(lambda t: "NONE")
        out = self.fanout(items=["a", "b"], reduce="merge")
        self.assertEqual(len(self.calls), 2)
        self.assertIn("0 with results (2 NONE omitted)", out)
        self.assertNotIn("reduced", out)

    def test_reduce_input_excludes_none(self):
        self.patch(lambda t: "NONE" if t == "a" else "hit")
        out = self.fanout(items=["a", "b"], reduce="merge")
        reduce_prompt = self.calls[-1]
        self.assertIn("## #2", reduce_prompt)
        self.assertNotIn("## #1", reduce_prompt)
        self.assertIn("1 with results (1 NONE omitted)", out)

    def test_output_dir_excludes_none(self):
        self.patch(lambda t: "NONE" if t == "a" else "hit")
        with tempfile.TemporaryDirectory() as d:
            out_dir = os.path.join(d, "out")
            out = self.fanout(items=["a", "b"], output_dir=out_dir)
            self.assertEqual(len(os.listdir(out_dir)), 1)
            self.assertTrue(os.listdir(out_dir)[0].startswith("002-"))
        self.assertIn("1 with results (1 NONE omitted)", out)

    def test_failures_still_shown(self):
        def fn(spec_, prompt, **kw):
            if prompt.endswith("BAD"):
                return {"ok": False, "error": "boom", "route": []}
            return _res("NONE")
        with mock.patch.object(P, "run", side_effect=fn):
            out = self.fanout(items=["a", "BAD"])
        self.assertIn("1/2 successful, 0 with results (1 NONE omitted)", out)
        self.assertIn("#2", out)
        self.assertIn("boom", out)

    def test_packed_path(self):
        self.patch(lambda t: "NONE" if t in ("x0", "x1") else f"A-{t}")
        out = self.fanout(items=[f"x{i}" for i in range(6)], pack=None)
        packs = [c for c in self.calls if "### ITEM " in c]
        self.assertEqual(len(packs), 1)
        self.assertIn(SUFFIX, packs[0])
        self.assertIn("4 with results (2 NONE omitted)", out)
        self.assertIn("- #3: A-x2", out)
        self.assertNotIn("- #1:", out)

    def test_summarizer_autopacks_only_with_drop_none(self):
        self.patch(lambda t: "NONE")
        self.fanout(role="summarizer", items=["a"] * 5, pack=None)
        self.assertEqual(len([c for c in self.calls if "### ITEM " in c]), 1)
        self.calls.clear()
        server.t_fanout({"role": "summarizer", "task": "scan", "items": ["a"] * 5, "concurrency": 1})
        self.assertEqual([c for c in self.calls if "### ITEM " in c], [])


class FilePackTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        p = mock.patch.object(server, "_roles", return_value=ROLES)
        p.start()
        self.addCleanup(p.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        def fn(spec_, prompt, **kw):
            self.calls.append(prompt)
            if "### ITEM " in prompt:
                return _res(json.dumps({n: "ok" for n, _ in _items_of(prompt)}))
            return _res("single")
        m = mock.patch.object(P, "run", side_effect=fn)
        m.start()
        self.addCleanup(m.stop)

    def write(self, name, text):
        p = os.path.join(self.tmp.name, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_small_files_pack_with_file_labels(self):
        paths = [self.write(f"f{i}.txt", f"content {i}") for i in range(6)]
        out = server.t_fanout({"role": "classifier", "task": "classify", "files": paths,
                               "concurrency": 1})
        self.assertEqual(len(self.calls), 1)
        self.assertIn("### ITEM 1", self.calls[0])
        self.assertIn("packed 6 items into 1 requests", out)
        for i in range(6):
            self.assertIn(f"- f{i}.txt: ok", out)

    def test_large_file_not_packed(self):
        paths = [self.write(f"f{i}.txt", "small") for i in range(5)]
        paths.append(self.write("big.txt", "x" * 3000))
        server.t_fanout({"role": "classifier", "task": "classify", "files": paths,
                         "concurrency": 1})
        self.assertEqual([c for c in self.calls if "### ITEM " in c], [])
        self.assertEqual(len(self.calls), 6)


if __name__ == "__main__":
    unittest.main()
