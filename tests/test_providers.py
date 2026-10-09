import _env  # noqa: F401  (must come first)
import json, os, shutil, time, unittest

import cache as C
import providers as P


class ChatSkipTest(unittest.TestCase):
    """chat() must never raise and must skip without a network call when it cannot run."""

    def test_unknown_provider(self):
        r = P.chat("nope", "hi")
        self.assertFalse(r["ok"])
        self.assertTrue(r["skipped"])

    def test_missing_key(self):
        r = P.chat("groq", "hi")
        self.assertTrue(r["skipped"])
        self.assertIn("missing key", r["error"])

    def test_input_over_budget(self):
        os.environ["GROQ_API_KEY"] = "test-not-a-real-key"
        try:
            r = P.chat("groq", "x" * (P.PROVIDERS["groq"]["max_input_chars"] + 1))
        finally:
            del os.environ["GROQ_API_KEY"]
        self.assertTrue(r["skipped"])
        self.assertIn("input too large", r["error"])

    def test_input_over_groq_budget_mentions_budget(self):
        os.environ["GROQ_API_KEY"] = "test-not-a-real-key"
        try:
            r = P.chat("groq", "x" * 30_000, max_tokens=7000)
        finally:
            del os.environ["GROQ_API_KEY"]
        self.assertTrue(r["skipped"])
        self.assertIn("budget", r["error"])
        self.assertIn("max_tokens 7000", r["error"])

    def test_fallback_reports_every_attempt(self):
        r = P.chat_with_fallback(["groq", "gemini", "openrouter"], "hi")
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["tried"]), 3)


class RolesTest(unittest.TestCase):
    def tearDown(self):
        if os.path.exists(P.CONFIG_FILE):
            os.remove(P.CONFIG_FILE)

    def test_language_rule_on_every_role(self):
        for name, spec in P.load_roles().items():
            self.assertTrue(spec["system"].endswith(P.LANGUAGE_RULE), name)

    def test_defaults_not_mutated(self):
        P.load_roles()
        self.assertNotIn(P.LANGUAGE_RULE, P.DEFAULT_ROLES["summarizer"]["system"])

    def test_config_merges_and_adds_roles(self):
        os.makedirs(P.CONFIG_DIR, exist_ok=True)
        with open(P.CONFIG_FILE, "w") as f:
            json.dump({"roles": {"coder": {"model": "other-model"},
                                 "seo": {"provider": "gemini", "system": "SEO editor."}}}, f)
        roles = P.load_roles()
        self.assertEqual(roles["coder"]["model"], "other-model")
        self.assertEqual(roles["coder"]["provider"], P.DEFAULT_ROLES["coder"]["provider"])
        self.assertTrue(roles["seo"]["system"].startswith("SEO editor."))
        self.assertTrue(roles["seo"]["system"].endswith(P.LANGUAGE_RULE))


class RetryDelayTest(unittest.TestCase):
    def test_retry_after_header_respected_and_capped(self):
        self.assertEqual(P._retry_delay({"retry-after": "3"}, 0), 3.5)
        self.assertEqual(P._retry_delay({"retry-after": "999"}, 0), 60)

    def test_exponential_backoff_without_header(self):
        self.assertEqual(P._retry_delay({}, 0), 2)
        self.assertEqual(P._retry_delay(None, 1), 4)
        self.assertEqual(P._retry_delay({}, 10), 20)



class InputBudgetTest(unittest.TestCase):
    def setUp(self):
        P._state_loaded = True
        P._itpm.clear()

    def tearDown(self):
        P._itpm.clear()

    def test_groq_budget_uses_output_allowance_not_max_tokens(self):
        b = P.input_budget("groq", 1500, "openai/gpt-oss-120b")
        self.assertEqual(b, P.input_budget("groq", 7000, "openai/gpt-oss-120b"))
        self.assertGreater(b, 18_000)
        self.assertLessEqual(b, 32_000)
        self.assertGreater(P.input_budget("groq", 100, "openai/gpt-oss-120b"), b)

    def test_itpm_lowers_budget(self):
        self.assertLess(P.input_budget("groq", 1024, "qwen/qwen3.8-27b"),
                        P.input_budget("groq", 1024, "openai/gpt-oss-120b"))

    def test_no_tpm_provider_and_unknown(self):
        self.assertEqual(P.input_budget("gemini", 4096), P.PROVIDERS["gemini"]["max_input_chars"])
        self.assertEqual(P.input_budget("nope", 100), 0)


class BucketTest(unittest.TestCase):
    def setUp(self):
        self.t = [1000.0]
        self.slept = []
        P._buckets.clear()

    def _sleep(self, s):
        self.slept.append(s)
        self.t[0] += s

    def acquire(self, key, need, limit=8000, block=True):
        return P._tpm_acquire(key, need, limit, now=lambda: self.t[0], sleep=self._sleep,
                              block=block)

    def test_starts_full_and_deducts(self):
        self.assertEqual(self.acquire("k", 1000), ("ok", 0.0))
        self.assertAlmostEqual(P._buckets["k"]["level"], 7000)

    def test_refill_math(self):
        self.acquire("k", 8000)
        self.t[0] += 30  # 8000/60 per second -> 4000
        self.assertTrue(P._tpm_room("k", 3900, 8000, now=lambda: self.t[0]))
        self.assertFalse(P._tpm_room("k", 4100, 8000, now=lambda: self.t[0]))
        self.t[0] += 600  # capped at the limit
        P._tpm_room("k", 1, 8000, now=lambda: self.t[0])
        self.assertAlmostEqual(P._buckets["k"]["level"], 8000)

    def test_waits_exact_time(self):
        self.acquire("k", 6000)           # level 2000
        st, waited = self.acquire("k", 5000)  # deficit 3000 / (8000/60) = 22.5 s
        self.assertEqual(st, "ok")
        self.assertAlmostEqual(waited, 22.5, delta=0.1)
        self.assertAlmostEqual(sum(self.slept), waited)

    def test_non_blocking_returns_busy_without_reserving(self):
        self.acquire("k", 7000)
        self.assertEqual(self.acquire("k", 5000, block=False), ("busy", None))
        self.assertEqual(self.slept, [])
        self.assertEqual(len(P._buckets["k"]["inflight"]), 1)

    def test_wait_over_max_is_long(self):
        self.acquire("k", 8000)
        st, wait = self.acquire("k", 7000)  # 52 s > 30
        self.assertEqual(st, "long")
        self.assertGreater(wait, P.TPM_MAX_WAIT)
        self.assertEqual(self.slept, [])

    def test_release_refunds(self):
        self.acquire("k", 7000)
        P._tpm_release("k", 7000, 8000, now=lambda: self.t[0])
        self.assertEqual(self.acquire("k", 7000), ("ok", 0.0))

    def test_sync_subtracts_other_inflight(self):
        self.acquire("k", 1000)
        self.acquire("k", 2000)
        P._tpm_sync("k", 1000, 5000, 8000, now=lambda: self.t[0])
        self.assertAlmostEqual(P._buckets["k"]["level"], 3000)  # 5000 - other inflight 2000
        self.assertEqual(P._buckets["k"]["inflight"], [2000.0])

    def test_settle_to_actual(self):
        self.acquire("k", 1000)  # level 7000
        P._tpm_settle("k", 1000, 400, 8000, now=lambda: self.t[0])
        self.assertAlmostEqual(P._buckets["k"]["level"], 7600)

    def test_penalize_sets_level_from_wait(self):
        self.acquire("k", 3000)
        P._tpm_penalize("k", 3000, 3.0, 8000, now=lambda: self.t[0])
        st, waited = self.acquire("k", 3000)
        self.assertEqual(st, "ok")
        self.assertAlmostEqual(waited, 3.0, delta=0.1)


import io, urllib.error
from unittest import mock


def _ok(text="ok", pt=0, ct=20, finish="stop"):
    return ({"choices": [{"message": {"content": text}, "finish_reason": finish}],
             "usage": {"prompt_tokens": pt, "completion_tokens": ct}}, {})


def _http(code, body="", headers=None):
    return urllib.error.HTTPError("http://x", code, "err", headers or {}, io.BytesIO(body.encode()))


class RouterTest(unittest.TestCase):
    def setUp(self):
        for k in ("GROQ_API_KEY", "GEMINI_API_KEY"):
            os.environ[k] = "test-not-a-real-key"
        P._state_loaded = True
        P._cooldown.clear()
        P._cpt.clear()
        P._buckets.clear()
        P._itpm.clear()
        shutil.rmtree(C.CACHE_DIR, ignore_errors=True)
        self.spec = {"provider": "groq", "model": "openai/gpt-oss-120b", "system": "sys",
                     "max_tokens": 2048, "min_tokens": 512}
        self.calls = []

    def tearDown(self):
        for k in ("GROQ_API_KEY", "GEMINI_API_KEY"):
            os.environ.pop(k, None)
        P._cooldown.clear()
        P._cpt.clear()
        P._itpm.clear()
        if os.path.exists(P.STATE_FILE):
            os.remove(P.STATE_FILE)

    def post(self, script):
        """script: list of results (or exceptions) consumed in call order."""
        it = iter(script)

        def fake(url, key, payload, timeout=180):
            self.calls.append((url, payload))
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return r
        return mock.patch.object(P, "_post", fake)

    def test_auto_small_input_uses_cap(self):
        with self.post([_ok()]):
            r = P.run(self.spec, "hello", role="t")
        self.assertTrue(r["ok"])
        self.assertEqual(r["route"], [])
        self.assertEqual(self.calls[0][1]["max_tokens"], 2048)

    def test_auto_medium_input_keeps_role_cap_on_groq(self):
        with self.post([_ok()]):
            r = P.run(self.spec, "x" * 18_000)
        self.assertEqual(r["provider"], "groq")
        self.assertEqual(self.calls[0][1]["max_tokens"], 2048)

    def test_huge_input_skips_groq_without_call(self):
        with self.post([_ok("from gemini")]):
            r = P.run(self.spec, "x" * 40_000)
        self.assertEqual(r["provider"], "gemini")
        self.assertEqual(len(self.calls), 1)
        self.assertIn("generativelanguage", self.calls[0][0])
        self.assertEqual(self.calls[0][1]["max_tokens"], 2048)
        self.assertEqual([x["model"] for x in r["route"]],
                         ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"])
        self.assertIn("input too large", r["route"][0]["error"])

    def test_pool_order_includes_20b_last(self):
        key = ("groq", "openai/gpt-oss-120b")
        P._buckets[key] = {"level": 0.0, "ts": time.time(), "inflight": []}
        P._buckets[("groq", "qwen/qwen3.8-27b")] = {"level": 0.0, "ts": time.time(), "inflight": []}
        with self.post([_ok("t")]):
            r = P.run(self.spec, "hi")
        self.assertEqual(r["model"], "openai/gpt-oss-20b")

    def test_itpm_learned_persisted_and_skips(self):
        body = ("Rate limit reached on input tokens per minute (ITPM): Limit 3000, Used 100, "
                "Requested 5388. Please try again in 25.44s.")
        prompt = "x" * 18_000  # ~5900 tokens: fits tpm, not the learned itpm
        script = [_http(429, body, {}), _ok("sib")]
        with self.post(script), mock.patch.object(P.time, "sleep") as sl:
            r = P.run(self.spec, prompt)
        self.assertEqual(P._itpm[("groq", "openai/gpt-oss-120b")], 3000)
        with open(P.STATE_FILE) as f:
            self.assertEqual(json.load(f)["itpm"]["groq|openai/gpt-oss-120b"], 3000)
        self.assertEqual(r["model"], "qwen/qwen3.8-27b")
        n = len(self.calls)
        with self.post([_ok("again")]):
            P.run(self.spec, prompt, no_cache=True)
        self.assertEqual(self.calls[n][1]["model"], "qwen/qwen3.8-27b")  # 120b skipped, no call

    def test_429_try_again_delay(self):
        body = "on tokens per minute (TPM): Limit 8000, Used 3075, Requested 5316. Please try again in 2.9325s."
        with self.post([_http(429, body), _ok("sib")]), mock.patch.object(P.time, "sleep") as sl:
            P.run(self.spec, "hi", no_fallback=True)
            # first candidate is non-last: breaks to sibling without sleeping
        sl.assert_not_called()
        self.assertAlmostEqual(P._retry_delay({}, 0, body), 3.18, places=2)

    def test_429_in_place_retry_waits_parsed_time(self):
        body = "Please try again in 2.9325s."
        spec = dict(self.spec, pool=[])
        clock = [time.time()]

        def fake_sleep(s):
            clock[0] += s
        with self.post([_http(429, body), _ok("late")]), \
                mock.patch.object(P.time, "sleep", side_effect=fake_sleep) as sl, \
                mock.patch.object(P.time, "time", lambda: clock[0]), \
                mock.patch.object(P, "PROVIDERS", {"groq": dict(P.PROVIDERS["groq"], pool=[])}):
            r = P.run(spec, "hi", no_fallback=True)
        self.assertEqual(r["text"], "late")
        delays = [c.args[0] for c in sl.call_args_list]
        self.assertTrue(any(abs(d - 3.18) < 0.05 for d in delays), delays)

    def test_would_wait_skips_to_next_provider(self):
        for m in ("openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"):
            P._buckets[("groq", m)] = {"level": -8000.0, "ts": time.time(), "inflight": []}
        with self.post([_ok("g")]):
            r = P.run(self.spec, "hi")
        self.assertEqual(r["provider"], "gemini")
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(any(x["error"].startswith("would wait") for x in r["route"]))

    def test_header_sync_after_success(self):
        res = ({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20}},
               {"x-ratelimit-remaining-tokens": "2182", "x-ratelimit-limit-tokens": "8000"})
        with self.post([res]):
            P.run(self.spec, "hi")
        b = P._buckets[("groq", "openai/gpt-oss-120b")]
        self.assertAlmostEqual(b["level"], 2182, delta=5)
        self.assertEqual(b["inflight"], [])

    def test_sibling_on_429_without_sleep(self):
        script = [_http(429, "tokens per minute (TPM)", {"retry-after": "3"}), _ok("sib")]
        with self.post(script), mock.patch.object(P.time, "sleep") as sl:
            r = P.run(self.spec, "hi")
        sl.assert_not_called()
        self.assertEqual(r["model"], "qwen/qwen3.8-27b")
        self.assertEqual(self.calls[1][1]["model"], "qwen/qwen3.8-27b")
        self.assertEqual(r["route"][0]["model"], "openai/gpt-oss-120b")

    def test_last_in_group_still_retries(self):
        script = [_http(429, "x", {"retry-after": "1"})] * 3 + [_ok("late")]
        clock = [time.time()]
        with self.post(script), \
                mock.patch.object(P.time, "sleep", side_effect=lambda x: clock.__setitem__(0, clock[0] + x)) as sl, \
                mock.patch.object(P.time, "time", lambda: clock[0]):
            r = P.run(self.spec, "hi")
        self.assertEqual(r["model"], "openai/gpt-oss-20b")  # last pool model waits and retries
        self.assertTrue(sl.called)
        self.assertEqual(len(self.calls), 4)

    def test_busy_sibling_is_skipped_not_waited(self):
        # primary window is full -> qwen goes first (room), primary stays patient last
        key = ("groq", "openai/gpt-oss-120b")
        P._buckets[key] = {"level": 100.0, "ts": time.time(), "inflight": []}
        with self.post([_ok("q")]):
            r = P.run(self.spec, "hi")
        self.assertEqual(r["model"], "qwen/qwen3.8-27b")

    def test_daily_limit_sets_cooldown(self):
        script = [_http(429, "Rate limit reached ... tokens per day (TPD)", {}), _ok("sib"),
                  _ok("again")]
        with self.post(script), mock.patch.object(P.time, "sleep") as sl:
            r = P.run(self.spec, "hi")
            self.assertEqual(r["model"], "qwen/qwen3.8-27b")
            self.assertIn(("groq", "openai/gpt-oss-120b"), P._cooldown)
            n = len(self.calls)
            r2 = P.run(self.spec, "hi", no_cache=True)
        sl.assert_not_called()
        self.assertEqual(len(self.calls), n + 1)  # primary skipped without a call
        self.assertEqual(r2["model"], "qwen/qwen3.8-27b")
        self.assertIn("cooling down", r2["route"][0]["error"])
        c = P.chat("groq", "hi", model="openai/gpt-oss-120b")
        self.assertTrue(c["skipped"])

    def test_truncated_auto_is_returned_not_escalated(self):
        # max_tokens is no longer shrunk to fit Groq, so auto-sized output is the role cap
        with self.post([_ok("cut", finish="length")]):
            r = P.run(self.spec, "x" * 20_000)
        self.assertEqual(r["provider"], "groq")
        self.assertTrue(r["truncated"])
        self.assertEqual(len(self.calls), 1)

    def test_truncated_auto_returned_if_rest_fails(self):
        os.environ.pop("GEMINI_API_KEY")
        with self.post([_ok("cut", finish="length")]):
            r = P.run(self.spec, "x" * 20_000)
        self.assertTrue(r["ok"])
        self.assertTrue(r["truncated"])

    def test_truncated_explicit_returned_as_is(self):
        with self.post([_ok("cut", finish="length")]):
            r = P.run(self.spec, "x" * 5000, max_tokens=1000)
        self.assertTrue(r["truncated"])
        self.assertEqual(r["provider"], "groq")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1]["max_tokens"], 1000)

    def test_explicit_model_has_no_siblings(self):
        with self.post([_http(429, "x", {"retry-after": "1"})] * 4 + [_ok("g")]), \
                mock.patch.object(P.time, "sleep"):
            r = P.run(self.spec, "hi", model="openai/gpt-oss-20b")
        models = [c[1]["model"] for c in self.calls]
        self.assertNotIn("qwen/qwen3.8-27b", models)
        self.assertEqual(r["provider"], "gemini")

    def test_no_fallback_keeps_siblings(self):
        with self.post([_http(429, "x", {}), _ok("sib")]), mock.patch.object(P.time, "sleep"):
            r = P.run(self.spec, "hi", no_fallback=True)
        self.assertEqual(r["model"], "qwen/qwen3.8-27b")

    def test_think_block_stripped(self):
        with self.post([_ok("<think>hmm\nmore</think>\n\nanswer")]):
            r = P.run(self.spec, "hi")
        self.assertEqual(r["text"], "answer")


class LearnedRatioTest(unittest.TestCase):
    def setUp(self):
        P._state_loaded = True
        P._cpt.clear()

    def tearDown(self):
        P._cpt.clear()
        if os.path.exists(P.STATE_FILE):
            os.remove(P.STATE_FILE)

    def test_default_then_learned_ema(self):
        self.assertEqual(P.chars_per_token("groq", "m"), P.CHARS_PER_TOKEN)
        P._learn_cpt("groq", "m", 4000, 1000)  # observed 4.0
        self.assertAlmostEqual(P.chars_per_token("groq", "m"), 0.7 * 3.2 + 0.3 * 4.0)
        self.assertAlmostEqual(P.chars_per_token("groq", "other"), P.chars_per_token("groq", "m"))
        self.assertEqual(P.chars_per_token("gemini", "m"), P.CHARS_PER_TOKEN)

    def test_clamped(self):
        for _ in range(60):
            P._learn_cpt("groq", "hi", 100000, 1)
        self.assertAlmostEqual(P.chars_per_token("groq", "hi"), P.CPT_MAX, places=3)
        for _ in range(60):
            P._learn_cpt("groq", "lo", 1, 100000)
        self.assertAlmostEqual(P.chars_per_token("groq", "lo"), P.CPT_MIN, places=3)

    def test_persisted(self):
        P._learn_cpt("groq", "m", 4000, 1000)
        with open(P.STATE_FILE) as f:
            self.assertIn("groq|m|ascii", json.load(f)["cpt"])

    def test_413_learns(self):
        P._learned_413("groq", "m", 9000, 1000, "Limit 8000, Requested 4000")  # 3000 input tokens
        self.assertAlmostEqual(P.chars_per_token("groq", "m"), 0.7 * 3.2 + 0.3 * 3.0)


class PerModelSnapshotTest(unittest.TestCase):
    def test_record_keeps_models_and_usage_reports_them(self):
        import tempfile, usage as U
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(P, "RATELIMIT_SNAPSHOT", os.path.join(d, "rl.json")), \
                mock.patch.object(P, "LEDGER", os.path.join(d, "ledger.jsonl")):
            res = {"ok": True, "tokens": {"in": 1, "out": 1}}
            P._record("groq", "openai/gpt-oss-120b", "t", res,
                      {"x-ratelimit-remaining-tokens": "2557", "x-ratelimit-limit-tokens": "8000",
                       "x-ratelimit-remaining-requests": "10", "x-ratelimit-limit-requests": "100"})
            P._record("groq", "qwen/qwen3.8-27b", "t", res,
                      {"x-ratelimit-remaining-tokens": "8000", "x-ratelimit-limit-tokens": "8000",
                       "x-ratelimit-remaining-requests": "10", "x-ratelimit-limit-requests": "100"})
            with open(P.RATELIMIT_SNAPSHOT) as f:
                snap = json.load(f)
            self.assertEqual(snap["groq"]["model"], "qwen/qwen3.8-27b")  # provider-level entry kept
            self.assertEqual(set(snap["groq"]["models"]), {"openai/gpt-oss-120b", "qwen/qwen3.8-27b"})
            with mock.patch.object(U, "_ledger", lambda since=None: []):
                recs = U.workers_usage()
            g = next(r for r in (recs["workers"] if isinstance(recs, dict) else recs)
                     if r["provider"] == "groq")
            models = g["quota"]["tokens"]["models"]
            self.assertEqual({m["model"] for m in models}, {"openai/gpt-oss-120b", "qwen/qwen3.8-27b"})
            m120 = next(m for m in models if m["model"].endswith("120b"))
            self.assertGreaterEqual(m120["remaining"], 2557)


if __name__ == "__main__":
    unittest.main()
