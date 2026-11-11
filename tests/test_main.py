"""Tests for the command line: modes, secrets, failures and what gets written."""

import functools
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from trending_site import feed as feed_model
from trending_site import gemini, job, sources, store
from trending_site.__main__ import main

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
TODAY = "2026-11-11"
ENV = {"GEMINI_API_KEY": "secret-key", "GITHUB_TOKEN": "gh-token"}
REAL_STEP = job.step


class FakeClient:
    def __init__(self, error=None):
        self.error = error

    def summarize(self, repo):
        if self.error:
            raise self.error
        return {"pt": repo["name"]}


def queued(name):
    return {"repo": {"name": name, "stars": 1, "readme": "r"}, "queued": "2026-11-10",
            "failures": 0, "last_failure": None}


class MainTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.source = self.dir / "feed.json"
        self.out = self.dir / "out"
        self.fetched = []
        self.client = FakeClient()
        self.factory = mock.Mock(return_value=self.client)
        self.fetch_result = ([{"name": "new/repo"}], [])
        self.write_feed(feed_model.empty())
        for target, value in (
            ("trending_site.job.now", lambda: NOW),
            ("trending_site.job.new_client", self.factory),
            ("trending_site.job.step",
             functools.partial(REAL_STEP, fetch=self.fetch, enrich=self.enrich)),
        ):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fetch(self, known):
        self.fetched.append(known)
        return self.fetch_result

    @staticmethod
    def enrich(repo):
        return {**repo, "stars": 3, "readme": "text"}

    def write_feed(self, feed):
        self.source.write_text(json.dumps(feed), encoding="utf-8")

    def run_main(self, command, env=ENV, extra=()):
        argv = [command, "--feed", str(self.source), "--out", str(self.out), *extra]
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertLogs(level="DEBUG") as logs:
                code = main(argv)
        self.logs = "\n".join(logs.output)
        return code

    def saved(self):
        return json.loads((self.out / "feed.json").read_text(encoding="utf-8"))

    def test_run_publishes_and_saves(self):
        self.assertEqual(self.run_main("run"), 0)
        feed = self.saved()
        self.assertEqual(feed["last_fetch"], TODAY)
        self.assertEqual(feed["generated_at"], NOW.isoformat())
        self.assertEqual(feed["status"], {"ok": True, "at": NOW.isoformat(), "reason": None})
        self.assertEqual(feed["days"][0]["repos"][0]["name"], "new/repo")
        self.factory.assert_called_once_with("secret-key", gemini.DEFAULT_MODEL)
        self.assertNotIn("secret-key", self.logs)

    def test_gemini_model_comes_from_the_environment(self):
        self.run_main("run", {**ENV, "GEMINI_MODEL": "other-model"})
        self.factory.assert_called_once_with("secret-key", "other-model")

    def test_retry_without_fetch_today_does_full_run(self):
        feed = feed_model.empty()
        feed["last_fetch"] = "2026-11-10"
        self.write_feed(feed)
        self.assertEqual(self.run_main("retry"), 0)
        self.assertEqual(len(self.fetched), 1)
        self.assertEqual(self.saved()["last_fetch"], TODAY)

    def test_retry_with_fetch_today_only_works_pending(self):
        feed = feed_model.empty()
        feed["last_fetch"] = TODAY
        feed["pending"] = [queued("a/old")]
        self.write_feed(feed)
        env = {"GEMINI_API_KEY": "secret-key"}
        self.assertEqual(self.run_main("retry", env), 0)
        self.assertEqual(self.fetched, [])
        saved = self.saved()
        self.assertEqual(saved["pending"], [])
        self.assertEqual(saved["days"][0]["repos"][0]["name"], "a/old")

    def test_retry_with_empty_pending_writes_nothing(self):
        feed = feed_model.empty()
        feed["last_fetch"] = TODAY
        feed_model.set_status(feed, False, "2026-11-11T06:00:00+00:00", "x")
        self.write_feed(feed)
        self.assertEqual(self.run_main("retry", {}), 0)
        self.assertFalse((self.out / "feed.json").exists())
        self.assertIn("nothing to do", self.logs)
        self.factory.assert_not_called()

    def test_missing_feed_writes_nothing(self):
        self.source.unlink()
        self.assertEqual(self.run_main("run"), 1)
        self.assertFalse((self.out / "feed.json").exists())
        self.assertIn("feed not found", self.logs)
        self.assertEqual(self.fetched, [])

    def test_first_run_starts_from_an_empty_feed(self):
        self.source.unlink()
        self.assertEqual(self.run_main("run", extra=["--first-run"]), 0)
        self.assertEqual(self.saved()["days"][0]["repos"][0]["name"], "new/repo")

    def test_invalid_feed_writes_nothing(self):
        self.source.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.run_main("run"), 1)
        self.assertFalse((self.out / "feed.json").exists())
        self.assertEqual(self.fetched, [])

    def test_missing_key_saves_failed_status(self):
        self.assertEqual(self.run_main("run", {"GITHUB_TOKEN": "gh-token"}), 1)
        status = self.saved()["status"]
        self.assertFalse(status["ok"])
        self.assertEqual(status["reason"], "chave do Gemini ausente")
        self.assertEqual(self.fetched, [])

    def test_missing_github_token_saves_failed_status(self):
        self.assertEqual(self.run_main("run", {"GEMINI_API_KEY": "secret-key"}), 1)
        self.assertEqual(self.saved()["status"]["reason"], "token do GitHub ausente")
        self.assertEqual(self.fetched, [])

    def test_gemini_auth_error_reason_is_its_message(self):
        self.client.error = gemini.AuthError("chave do Gemini rejeitada")
        self.assertEqual(self.run_main("run"), 1)
        feed = self.saved()
        self.assertEqual(feed["status"]["reason"], "chave do Gemini rejeitada")
        self.assertEqual(feed["pending"][0]["repo"]["name"], "new/repo")

    def test_github_auth_error_is_token_rejected(self):
        def enrich(repo):
            raise sources.GitHubAuthError("401")

        step = functools.partial(REAL_STEP, fetch=self.fetch, enrich=enrich)
        with mock.patch("trending_site.job.step", step):
            self.assertEqual(self.run_main("run"), 1)
        self.assertEqual(self.saved()["status"]["reason"], "token do GitHub rejeitado")

    def test_unexpected_error_reason_is_its_repr(self):
        self.client.error = RuntimeError("x" * 300)
        self.assertEqual(self.run_main("run"), 1)
        reason = self.saved()["status"]["reason"]
        self.assertTrue(reason.startswith("RuntimeError('xxx"))
        self.assertEqual(len(reason), 200)

    def test_step_failure_reason_fails_the_run(self):
        self.fetch_result = ([], [{"source": "trendshift", "error": "a"},
                                  {"source": "github", "error": "b"}])
        self.assertEqual(self.run_main("run"), 1)
        status = self.saved()["status"]
        self.assertEqual(status, {"ok": False, "at": NOW.isoformat(),
                                  "reason": "Trendshift e GitHub Trending falharam"})


    def test_feed_without_last_fetch_runs(self):
        feed = feed_model.empty()
        del feed["last_fetch"]
        self.write_feed(feed)
        self.assertEqual(self.run_main("retry"), 0)
        self.assertEqual(len(self.fetched), 1)
        self.assertEqual(self.saved()["last_fetch"], TODAY)

    def test_invalid_result_writes_nothing(self):
        def step(feed, client, now, *, fetch_sources):
            repo = {"name": "a/dup", "stars": 1, "summary": None}
            feed["days"] = [{"date": TODAY, "failed": [], "repos": [repo]},
                            {"date": "2026-11-10", "failed": [], "repos": [dict(repo)]}]
            return None

        with mock.patch("trending_site.job.step", step):
            self.assertEqual(self.run_main("run"), 1)
        self.assertFalse((self.out / "feed.json").exists())
        self.assertIn("a repo appears more than once", self.logs)


class FeedDefaultTest(unittest.TestCase):
    def test_feed_defaults_to_the_pages_url(self):
        self.assertEqual(job.FEED_URL,
                         "https://emanuel36.github.io/trending-repos-site/data/feed.json")
        with mock.patch("trending_site.store.load", side_effect=store.StoreError("x")) as load:
            with self.assertLogs(level="ERROR"):
                self.assertEqual(main(["run"]), 1)
        load.assert_called_once_with(job.FEED_URL, allow_missing=False)


if __name__ == "__main__":
    unittest.main()
