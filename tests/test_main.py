"""Tests for the command line."""

import pathlib
import tempfile
import unittest
from unittest import mock

from trending_site import __main__ as cli
from trending_site.state import State


class MainTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        self.env = {"TRENDING_STATE_DIR": str(root / "state"),
                    "TRENDING_SITE_DIR": str(root / "site"),
                    "GEMINI_API_KEY": "key", "GITHUB_TOKEN": "token"}
        self.state = State(root / "state")

    def main(self, env):
        with mock.patch.dict("os.environ", env, clear=True), \
                mock.patch("trending_site.run.run", return_value=0) as run, \
                mock.patch("trending_site.sources.fetch") as fetch:
            result = cli.main(["run"])
        return result, run, fetch

    def test_runs_with_both_keys(self):
        result, run, _fetch = self.main(self.env)
        self.assertEqual(result, 0)
        run.assert_called_once()

    def test_missing_gemini_key_is_a_failed_run(self):
        result, run, _fetch = self.main({**self.env, "GEMINI_API_KEY": ""})
        self.assertEqual(result, 1)
        run.assert_not_called()
        self.assertEqual(self.state.status()["reason"], "chave do Gemini ausente")

    def test_invalid_max_repos_is_a_failed_run(self):
        result, run, fetch = self.main({**self.env, "TRENDING_MAX_REPOS": "trinta"})
        self.assertEqual(result, 1)
        run.assert_not_called()
        fetch.assert_not_called()
        self.assertEqual(self.state.status()["reason"], "TRENDING_MAX_REPOS inválido: trinta")
        self.assertFalse(self.state.status()["ok"])
        index = pathlib.Path(self.env["TRENDING_SITE_DIR"]) / "index.html"
        self.assertIn("TRENDING_MAX_REPOS inválido: trinta", index.read_text())

    def test_missing_github_token_is_a_failed_run(self):
        without = {key: value for key, value in self.env.items() if key != "GITHUB_TOKEN"}
        for env in (without, {**self.env, "GITHUB_TOKEN": ""}):
            with self.subTest(env=env):
                result, run, fetch = self.main(env)
                self.assertEqual(result, 1)
                run.assert_not_called()
                fetch.assert_not_called()
                self.assertEqual(self.state.status()["reason"], "token do GitHub ausente")
                self.assertFalse(self.state.status()["ok"])


if __name__ == "__main__":
    unittest.main()
