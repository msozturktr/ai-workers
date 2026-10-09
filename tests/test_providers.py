import _env  # noqa: F401  (must come first)
import json, os, unittest

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
            r = P.chat("groq", "x" * 10_000, max_tokens=7000)
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
    def test_groq_scales_with_max_tokens(self):
        b = P.input_budget("groq", 1500)
        self.assertGreater(b, 12_000)
        self.assertLessEqual(b, 24_000)
        self.assertLess(P.input_budget("groq", 7000), 3000)

    def test_no_tpm_provider_and_unknown(self):
        self.assertEqual(P.input_budget("gemini", 4096), P.PROVIDERS["gemini"]["max_input_chars"])
        self.assertEqual(P.input_budget("nope", 100), 0)


class TpmAcquireTest(unittest.TestCase):
    def setUp(self):
        self.t = [1000.0]
        self.slept = []

    def _sleep(self, s):
        self.slept.append(s)
        self.t[0] += s

    def acquire(self, key, need, limit):
        return P._tpm_acquire(key, need, limit, now=lambda: self.t[0], sleep=self._sleep)

    def test_no_wait_when_room(self):
        self.assertEqual(self.acquire("k-room", 1000, 8000), 0)
        self.assertEqual(self.slept, [])

    def test_waits_until_entries_expire(self):
        self.acquire("k-full", 5000, 8000)
        self.t[0] += 40
        waited = self.acquire("k-full", 5000, 8000)
        self.assertGreaterEqual(waited, 20)
        self.assertLess(waited, 21)

    def test_never_waits_more_than_30s(self):
        self.acquire("k-cap", 7000, 8000)
        waited = self.acquire("k-cap", 7000, 8000)
        self.assertLessEqual(waited, 30.0)
        self.assertAlmostEqual(sum(self.slept), waited)


class TpmReleaseTest(unittest.TestCase):
    def test_release_frees_room(self):
        key = ("test", "release")
        P._tpm_windows.pop(key, None)
        clock = [1000.0]
        P._tpm_acquire(key, 7000, 8000, now=lambda: clock[0], sleep=lambda s: None)
        P._tpm_release(key, 7000)
        waited = P._tpm_acquire(key, 7000, 8000, now=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s))
        self.assertEqual(waited, 0.0)


if __name__ == "__main__":
    unittest.main()
