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
