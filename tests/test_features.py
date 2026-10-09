import _env  # noqa: F401  (must come first)
import os, shutil, tempfile, time, unittest
from unittest import mock

import cache as C
import providers as P
import server

SPEC = {"provider": "groq", "model": "openai/gpt-oss-120b", "system": "sys",
        "max_tokens": 2048, "min_tokens": 512}


def _res(text="answer", **kw):
    r = {"ok": True, "provider": "groq", "model": "openai/gpt-oss-120b", "text": text,
         "tokens": {"in": 1, "out": 1}}
    r.update(kw)
    return r


class CacheTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(C.CACHE_DIR, ignore_errors=True)
        P._state_loaded = True
        P._cooldown.clear()
        P._buckets.clear()

    def test_hit_avoids_chat(self):
        with mock.patch.object(P, "chat", return_value=_res()) as ch:
            a = P.run(SPEC, "hello", role="t")
            b = P.run(SPEC, "hello", role="t")
        self.assertEqual(ch.call_count, 1)
        self.assertNotIn("cached", a)
        self.assertTrue(b["cached"])
        self.assertEqual(b["route"], [])
        self.assertEqual(b["text"], "answer")

    def test_different_prompt_or_max_tokens_misses(self):
        with mock.patch.object(P, "chat", return_value=_res()) as ch:
            P.run(SPEC, "hello")
            P.run(SPEC, "hello2")
            P.run(SPEC, "hello", max_tokens=300)
        self.assertEqual(ch.call_count, 3)

    def test_no_cache_bypasses(self):
        with mock.patch.object(P, "chat", return_value=_res()) as ch:
            P.run(SPEC, "hello")
            r = P.run(SPEC, "hello", no_cache=True)
        self.assertEqual(ch.call_count, 2)
        self.assertNotIn("cached", r)

    def test_truncated_and_warning_not_stored(self):
        for extra in ({"truncated": True}, {"warning": "empty"}):
            with mock.patch.object(P, "chat", return_value=_res(**extra)) as ch:
                P.run(SPEC, "p")
                P.run(SPEC, "p")
            self.assertEqual(ch.call_count, 2, extra)
            shutil.rmtree(C.CACHE_DIR, ignore_errors=True)

    def test_failure_and_empty_not_stored(self):
        k = C.make_key(a=1)
        C.put(k, {"ok": False, "error": "x"})
        C.put(k, _res(text="  "))
        self.assertIsNone(C.get(k))

    def test_ttl_expiry(self):
        k = C.make_key(a=2)
        C.put(k, _res())
        self.assertEqual(C.get(k)["text"], "answer")
        self.assertIsNone(C.get(k, ttl=-1))
        self.assertIsNone(C.get(k))  # expired entry was deleted

    def test_route_not_stored(self):
        k = C.make_key(a=3)
        C.put(k, _res(route=[{"provider": "x"}]))
        self.assertNotIn("route", C.get(k))

    def test_prune_keeps_newest(self):
        keys = []
        for i in range(6):
            k = C.make_key(i=i)
            keys.append(k)
            C.put(k, _res(text=f"t{i}"))
            os.utime(C._path(k), (1000 + i, 1000 + i))
        with mock.patch.object(C, "MAX_FILES", 3):
            C.prune()
        left = [k for k in keys if os.path.exists(C._path(k))]
        self.assertEqual(left, keys[-3:])


class OutputFileTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(dir=_env.HOME)
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.run_mock = mock.patch.object(P, "run", return_value=_res("line1\n\nline2\nline3\nline4"))
        self.run = self.run_mock.start()
        self.addCleanup(self.run_mock.stop)

    def call(self, **kw):
        return server.t_delegate({"role": "summarizer", "task": "t", "input": "x", **kw})

    def test_written_and_preview(self):
        path = os.path.join(self.d, "sub", "out.md")
        out = self.call(output_file=path)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "line1\n\nline2\nline3\nline4")
        self.assertIn(f"saved 24 chars, 5 lines -> {path}", out)
        self.assertIn("line1\nline2\nline3", out)
        self.assertNotIn("line4", out)
        self.assertTrue(out.startswith("[summarizer -> groq/openai/gpt-oss-120b]"))

    def test_truncation_note_returned_not_written(self):
        self.run.return_value = _res("abc", truncated=True)
        path = os.path.join(self.d, "t.md")
        out = self.call(output_file=path)
        self.assertIn("TRUNCATED", out)
        with open(path) as f:
            self.assertEqual(f.read(), "abc")

    def test_refuses_existing_without_overwrite(self):
        path = os.path.join(self.d, "e.md")
        with open(path, "w") as f:
            f.write("old")
        self.assertTrue(self.call(output_file=path).startswith("ERROR"))
        self.assertEqual(self.run.call_count, 0)
        self.assertIn("saved", self.call(output_file=path, overwrite=True))
        with open(path) as f:
            self.assertEqual(f.read(), "line1\n\nline2\nline3\nline4")

    def test_refuses_secret_path(self):
        out = self.call(output_file=os.path.join(self.d, "my.env"))
        self.assertTrue(out.startswith("ERROR"))
        self.assertEqual(self.run.call_count, 0)

    def test_refuses_relative_path(self):
        self.assertTrue(self.call(output_file="rel/out.md").startswith("ERROR"))
        self.assertEqual(self.run.call_count, 0)

    def test_cached_marker_in_head(self):
        self.run.return_value = _res(cached=True)
        self.assertIn("openai/gpt-oss-120b cached]", self.call())


class FanoutTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(dir=_env.HOME)
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.prompts = []

    def patch(self, fn):
        m = mock.patch.object(P, "run", side_effect=fn)
        m.start()
        self.addCleanup(m.stop)

    def fanout(self, **kw):
        return server.t_fanout({"role": "summarizer", "task": "t", "concurrency": 1, **kw})

    def test_output_dir_files(self):
        self.patch(lambda spec, prompt, **kw: _res(f"out:{prompt[-1]}"))
        out_dir = os.path.join(self.d, "o")
        out = self.fanout(items=[{"input": "a", "label": "Foo Bar.txt"}, "b"], output_dir=out_dir)
        self.assertEqual(sorted(os.listdir(out_dir)), ["001-foo-bar-txt.md", "002-2.md"])
        self.assertIn(f"- Foo Bar.txt [groq/openai/gpt-oss-120b] -> 001-foo-bar-txt.md (5 chars)", out)
        self.assertNotIn("out:a", out)

    def test_output_dir_nonempty_refused(self):
        self.patch(lambda spec, prompt, **kw: _res())
        out_dir = os.path.join(self.d, "o")
        os.makedirs(out_dir)
        with open(os.path.join(out_dir, "x"), "w") as f:
            f.write("1")
        self.assertTrue(self.fanout(items=["a"], output_dir=out_dir).startswith("ERROR"))
        self.assertTrue(self.fanout(items=["a"], output_dir=out_dir, overwrite=True).startswith("# fanout"))
        self.assertTrue(self.fanout(items=["a"], output_dir=os.path.join(self.d, "k.env")).startswith("ERROR"))

    def _reduce_fn(self, fail_on=()):
        def fn(spec, prompt, **kw):
            if kw.get("role") == "reducer":
                self.prompts.append(prompt)
                return _res("MERGED")
            if any(f in prompt for f in fail_on):
                return {"ok": False, "error": "boom", "route": []}
            return _res("res-" + prompt.rsplit("\n", 1)[-1])
        return fn

    def test_reduce_receives_outputs_in_order_and_lists_failures(self):
        self.patch(self._reduce_fn(fail_on=("BAD",)))
        with mock.patch.object(server, "_roles", return_value={
                "summarizer": dict(SPEC), "reducer": dict(SPEC)}):
            out = self.fanout(items=[{"input": "one", "label": "A"}, {"input": "BAD", "label": "B"},
                                     {"input": "three", "label": "C"}],
                              reduce="merge", reduce_role="reducer")
        self.assertEqual(len(self.prompts), 1)
        p = self.prompts[0]
        self.assertLess(p.index("## A\nres-one"), p.index("## C\nres-three"))
        self.assertNotIn("## B", p)
        self.assertTrue(p.startswith("merge"))
        self.assertTrue(out.startswith("# fanout: 2/3 successful -> reduced [reducer -> groq/"))
        self.assertIn("MERGED", out)
        self.assertIn("## Failed jobs\n- B: boom", out)
        self.assertNotIn("res-one", out)

    def test_all_failed_skips_reduce(self):
        self.patch(self._reduce_fn(fail_on=("x",)))
        out = self.fanout(items=["x1", "x2"], reduce="merge")
        self.assertEqual(self.prompts, [])
        self.assertTrue(out.startswith("# fanout: 0/2 successful"))
        self.assertIn("[FAILED]", out)

    def test_reduce_with_output_file(self):
        self.patch(self._reduce_fn())
        path = os.path.join(self.d, "merged.md")
        with mock.patch.object(server, "_roles", return_value={
                "summarizer": dict(SPEC), "reducer": dict(SPEC)}):
            out = self.fanout(items=["a", "b"], reduce="merge", reduce_role="reducer",
                              output_file=path)
        with open(path) as f:
            self.assertEqual(f.read(), "MERGED")
        self.assertIn(f"saved 6 chars, 1 lines -> {path}", out)

    def test_output_file_without_reduce_refused(self):
        self.patch(self._reduce_fn())
        out = self.fanout(items=["a"], output_file=os.path.join(self.d, "m.md"))
        self.assertTrue(out.startswith("ERROR"))


class ScriptClassTest(unittest.TestCase):
    def setUp(self):
        P._state_loaded = True
        P._cpt.clear()
        self.addCleanup(P._cpt.clear)
        self.addCleanup(lambda: os.path.exists(P.STATE_FILE) and os.remove(P.STATE_FILE))

    def test_classes(self):
        self.assertEqual(P.script_class("plain english text"), "ascii")
        self.assertEqual(P.script_class("Şu çağrıştırıcı ığüşöç metin"), "intl")
        self.assertEqual(P.script_class(""), "ascii")

    def test_defaults_differ(self):
        self.assertEqual(P.chars_per_token("groq", "m", "intl"), P.CHARS_PER_TOKEN_INTL)
        self.assertGreater(P.est_tokens("groq", "m", 1000, "intl"), P.est_tokens("groq", "m", 1000, "ascii"))

    def test_learning_ascii_does_not_change_intl(self):
        P._learn_cpt("groq", "m", 4000, 1000, "ascii")
        self.assertEqual(P.chars_per_token("groq", "m", "intl"), P.CHARS_PER_TOKEN_INTL)
        self.assertEqual(P.chars_per_token("groq", "other", "intl"), P.CHARS_PER_TOKEN_INTL)
        P._learn_cpt("groq", "m", 2000, 1000, "intl")
        self.assertAlmostEqual(P.chars_per_token("groq", "m", "intl"), 0.7 * 2.6 + 0.3 * 2.0)
        self.assertAlmostEqual(P.chars_per_token("groq", "other", "intl"),
                               P.chars_per_token("groq", "m", "intl"))

    def test_old_state_keys_ignored(self):
        import json
        os.makedirs(P.CONFIG_DIR, exist_ok=True)
        with open(P.STATE_FILE, "w") as f:
            json.dump({"cpt": {"groq|m": 4.0, "groq|n|intl": 2.2}}, f)
        P._state_loaded = False
        self.assertEqual(P.chars_per_token("groq", "m"), P.CHARS_PER_TOKEN)
        self.assertEqual(P.chars_per_token("groq", "n", "intl"), 2.2)


if __name__ == "__main__":
    unittest.main()
