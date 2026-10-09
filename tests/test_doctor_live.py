import _env  # noqa: F401  (must come first)
import io, unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

import cli
import providers as P

ROLES = {
    "a": {"provider": "groq", "model": "openai/gpt-oss-120b", "reasoning_effort": {"groq": "low", "gemini": "minimal"},
          "fallback_models": {"gemini": "gemini-3.5-flash-lite"}},
    "b": {"provider": "groq", "model": "openai/gpt-oss-20b", "reasoning_effort": {"groq": "low"}},
    "c": {"provider": "gemini", "model": "gemini-3.6-flash"},
}


class TargetsTest(unittest.TestCase):
    def test_collect_and_dedupe(self):
        t = cli.live_targets(ROLES)
        self.assertEqual(len(t), len(set(t)))
        self.assertIn(("groq", "openai/gpt-oss-120b", "low"), t)
        self.assertIn(("groq", "openai/gpt-oss-20b", "low"), t)
        self.assertIn(("groq", "qwen/qwen3.8-27b", None), t)  # pool, effort not sent to qwen
        self.assertIn(("gemini", "gemini-3.5-flash-lite", "minimal"), t)  # fallback_models
        self.assertIn(("gemini", "gemini-3.6-flash", None), t)  # c primary / b's default
        self.assertIn(("openrouter", P.PROVIDERS["openrouter"]["default_model"], None), t)
        self.assertEqual(t.count(("groq", "openai/gpt-oss-120b", "low")), 1)
        # c (gemini) falls back to groq default with no effort
        self.assertIn(("groq", "openai/gpt-oss-120b", None), t)


def run_live(chat, cooling=lambda p, m: None, have=("groq", "gemini", "openrouter"), **kw):
    buf = io.StringIO()
    with mock.patch.object(cli.P, "load_roles", return_value=ROLES), \
         mock.patch.object(cli.P, "available_providers", return_value=list(have)), \
         mock.patch.object(cli.P, "_cooling", side_effect=cooling), \
         mock.patch.object(cli.P, "chat", side_effect=chat) as c, redirect_stdout(buf):
        ok = cli.doctor_live(**kw)
    return ok, buf.getvalue(), c


class OutputTest(unittest.TestCase):
    def test_ok_fail_cooling_hints(self):
        def chat(prov, prompt, **kw):
            m = kw["model"]
            if m == "gemini-3.5-flash-lite":
                return {"ok": False, "error": "HTTP 400: reasoning_effort bad"}
            if m == "qwen/qwen3.8-27b":
                return {"ok": False, "error": "HTTP 404: model_not_found"}
            return {"ok": True}
        cool = lambda p, m: "cooling down until 12:34: rpd" if m == "openai/gpt-oss-20b" else None
        ok, out, c = run_live(chat, cool)
        self.assertFalse(ok)
        self.assertRegex(out, r"\[OK     \] groq openai/gpt-oss-120b effort=low  \d+\.\d\d s")
        self.assertIn("[FAIL   ] gemini gemini-3.5-flash-lite effort=minimal  HTTP 400", out)
        self.assertIn("[COOLING] groq openai/gpt-oss-20b effort=low until 12:34: rpd", out)
        self.assertIn("run: ai-workers models groq", out)
        self.assertIn("may be unsupported", out)
        for call in c.call_args_list:
            self.assertEqual(call.kwargs["role"], "doctor")
            self.assertEqual(call.kwargs["retries"], 1)

    def test_all_ok(self):
        ok, out, _ = run_live(lambda p, pr, **kw: {"ok": True})
        self.assertTrue(ok)
        self.assertIn("openrouter request", out)

    def test_no_openrouter(self):
        ok, out, c = run_live(lambda p, pr, **kw: {"ok": True}, no_openrouter=True)
        self.assertNotIn("openrouter", out)
        self.assertTrue(all(call.args[0] != "openrouter" for call in c.call_args_list))

    def test_skip_no_key(self):
        ok, out, c = run_live(lambda p, pr, **kw: {"ok": True}, have=("groq",))
        self.assertIn("[SKIP   ] gemini", out)
        self.assertTrue(ok)


class PlainDoctorTest(unittest.TestCase):
    def test_no_chat_calls(self):
        buf = io.StringIO()
        with mock.patch.object(cli.P, "chat") as c, mock.patch.object(cli.U, "claude_live", return_value={"pending": True}), \
             mock.patch.object(cli, "dashboard_up", return_value=False), \
             mock.patch.object(cli.shutil, "which", return_value=None), redirect_stdout(buf):
            cli.cmd_doctor(SimpleNamespace(live=False, no_openrouter=False))
        c.assert_not_called()


if __name__ == "__main__":
    unittest.main()
