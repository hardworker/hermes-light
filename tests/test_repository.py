"""Verify privacy checks include documentation and removed historical files."""
from contextlib import redirect_stdout
import importlib.util
from io import StringIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hygiene", ROOT / "scripts/check_repository.py")
hygiene = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hygiene)


class RepositoryTests(unittest.TestCase):
    def test_sensitive_values_in_markdown_are_reported_without_echoing_them(self):
        token = "gh" + "p_" + "a" * 30
        findings = hygiene.findings("README.md", f"setup\n{token}\n".encode())
        self.assertTrue(any("README.md:2:" in message for message in findings))
        self.assertNotIn(token, "\n".join(findings))
        self.assertTrue(hygiene.findings("signing/certificate.p12", b"fixture"))
        self.assertTrue(hygiene.findings(".env", b"fixture"))
        self.assertFalse(hygiene.findings("README.md", b"Sign in locally. Example: you@example.com"))

    def test_deleted_sensitive_document_is_still_found_in_history(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            def git(*args):
                return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
            git("init", "-q")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.com")
            token = "gh" + "p_" + "a" * 30
            doc = repo / "README.md"
            doc.write_text(token)
            git("add", "README.md")
            git("commit", "-qm", "Synthetic fixture")
            doc.write_text("Generic documentation")
            git("add", "README.md")
            git("commit", "-qm", "Remove synthetic fixture")
            output = StringIO()
            with patch.object(hygiene, "ROOT", repo), redirect_stdout(output):
                self.assertEqual(hygiene.check(), 0)
                self.assertEqual(hygiene.check(history=True), 1)
            self.assertIn("history", output.getvalue())
            self.assertNotIn(token, output.getvalue())


if __name__ == "__main__":
    unittest.main()
