import _env  # noqa: F401  (must come first)
import os, tempfile, unittest

import sources as S


def write(path, data="x\n", mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as f:
        f.write(data)
    return path


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(dir=_env.HOME)

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    def test_relative_path_rejected(self):
        files, _, err = S.resolve(["relative/file.txt"])
        self.assertEqual(files, [])
        self.assertIn("absolute path required", err)

    def test_tilde_expands(self):
        write(self.p("a.txt"))
        rel = "~/" + os.path.relpath(self.p("a.txt"), _env.HOME)
        files, _, err = S.resolve([rel])
        self.assertIsNone(err)
        self.assertEqual(files, [self.p("a.txt")])

    def test_directory_expands_and_skips_tool_dirs(self):
        write(self.p("src", "main.py"))
        write(self.p("node_modules", "lib", "index.js"))
        write(self.p(".git", "config"))
        files, _, err = S.resolve([self.root])
        self.assertIsNone(err)
        self.assertEqual(files, [self.p("src", "main.py")])

    def test_explicit_path_inside_tool_dir_is_allowed(self):
        f = write(self.p("node_modules", "pkg", "README.md"))
        files, _, _ = S.resolve([f])
        self.assertEqual(files, [f])

    def test_secret_files_never_returned(self):
        for name in (".env", ".env.local", "server.pem", "id_rsa", "credentials.json",
                     "my_secret.txt", "token-cache.json"):
            write(self.p(name))
        files, skipped, _ = S.resolve([self.p("*"), self.p(".*")])
        self.assertEqual(files, [])
        self.assertTrue(all("secret" in s["reason"] for s in skipped))

    def test_secret_dirs_never_returned(self):
        key = write(os.path.join(_env.HOME, ".ssh", "config"))
        files, skipped, _ = S.resolve([key])
        self.assertEqual(files, [])
        self.assertEqual(skipped[0]["reason"], "secret file (not sent)")

    def test_unmatched_glob_reported(self):
        files, skipped, err = S.resolve([self.p("*.nothing")])
        self.assertIsNone(err)
        self.assertEqual(files, [])
        self.assertEqual(skipped[0]["reason"], "no matching files")

    def test_too_many_files(self):
        for i in range(S.MAX_FILES + 2):
            write(self.p("many", f"{i}.txt"))
        _, _, err = S.resolve([self.p("many")])
        self.assertIn("too many files", err)


class ReadAndChunkTest(unittest.TestCase):
    def test_binary_detected(self):
        f = write(os.path.join(tempfile.mkdtemp(dir=_env.HOME), "b.bin"), b"\x00\x01abc", "wb")
        self.assertIsNone(S.read_text(f))

    def test_chunk_respects_line_boundaries(self):
        text = "".join(f"line {i:03d}\n" for i in range(100))  # 9 chars per line
        parts = S.chunk(text, 50)
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(p) <= 50 for p in parts))
        self.assertTrue(all(p.endswith("\n") for p in parts))

    def test_chunk_hard_splits_long_line(self):
        parts = S.chunk("a" * 25, 10)
        self.assertEqual(parts, ["a" * 10, "a" * 10, "a" * 5])

    def test_small_text_single_chunk(self):
        self.assertEqual(S.chunk("abc", 10), ["abc"])


class BundleItemsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(dir=_env.HOME)
        write(os.path.join(self.root, "a.txt"), "alpha\n")
        write(os.path.join(self.root, "b.txt"), "beta\n" * 40)
        write(os.path.join(self.root, "c.bin"), b"\x00\x00", "wb")

    def test_bundle_labels_and_skips_binary(self):
        text, n, skipped, err = S.bundle([self.root])
        self.assertIsNone(err)
        self.assertEqual(n, 2)
        self.assertIn("### FILE: a.txt\nalpha", text)
        self.assertEqual([s["reason"] for s in skipped], ["binary file"])

    def test_items_chunk_large_files(self):
        out, _, err = S.items([self.root], chunk_chars=60)
        self.assertIsNone(err)
        labels = [o["label"] for o in out]
        self.assertEqual(labels[0], "a.txt")
        self.assertTrue(labels[1].startswith("b.txt [1/"))
        self.assertTrue(all(o["input"].startswith(f"### FILE: {o['label']}\n") for o in out))


if __name__ == "__main__":
    unittest.main()
