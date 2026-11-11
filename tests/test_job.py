"""Tests for one step of the daily job."""

import unittest
from datetime import UTC, datetime

from trending_site import feed as feed_model
from trending_site import gemini, sources
from trending_site.job import looks_like_spam, step, today

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
TODAY = "2026-11-11"


class FakeClient:
    """Summarizes every repo, or raises `error` for each one."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def summarize(self, repo):
        self.calls.append(repo["name"])
        if self.error:
            raise self.error
        return {"pt": repo["name"]}


def fake_fetch(repos, failed=()):
    calls = []

    def fetch(known):
        calls.append(set(known))
        return [dict(r) for r in repos], list(failed)

    fetch.calls = calls
    return fetch


def fake_enrich(repo):
    if repo["name"].startswith("broken/"):
        raise sources.EnrichFailed(f"{repo['name']}: rate limited")
    return {**repo, "stars": 10, "readme": "text"}


def queued(name, day="2026-11-10"):
    return {"repo": {"name": name, "stars": 5, "readme": "r"}, "queued": day, "failures": 0,
            "last_failure": None}


def run_step(feed, client, fetch, fetch_sources=True):
    return step(feed, client, NOW, fetch_sources=fetch_sources, fetch=fetch, enrich=fake_enrich)


class TodayTest(unittest.TestCase):
    def test_today_is_sao_paulo_date(self):
        self.assertEqual(today(datetime(2026, 11, 12, 2, 0, tzinfo=UTC)), "2026-11-11")


class StepTest(unittest.TestCase):
    def test_run_publishes_and_records_last_fetch(self):
        feed = feed_model.empty()
        failed = [{"source": "github", "error": "boom"}]
        fetch = fake_fetch([{"name": "a/one"}, {"name": "broken/two"}], failed)
        with self.assertLogs("trending_site.job", "WARNING") as logs:
            reason = run_step(feed, FakeClient(), fetch)
        self.assertIsNone(reason)
        self.assertEqual(feed["last_fetch"], TODAY)
        self.assertEqual(feed["pending"], [])
        self.assertEqual(feed["days"], [{"date": TODAY, "failed": failed, "repos": [
            {"name": "a/one", "stars": 10, "summary": {"pt": "a/one"}}]}])
        self.assertTrue(any("github" in line for line in logs.output))
        self.assertTrue(any("broken/two" in line for line in logs.output))
        self.assertEqual(feed_model.problems(feed), [])

    def test_fetch_gets_the_known_names(self):
        feed = feed_model.empty()
        feed["pending"].append(queued("Old/Repo"))
        fetch = fake_fetch([])
        run_step(feed, FakeClient(), fetch)
        self.assertEqual(fetch.calls, [{"old/repo"}])

    def test_queued_repo_trending_again_is_not_duplicated(self):
        feed = feed_model.empty()
        feed["pending"].append(queued("Owner/Repo"))
        fetch = fake_fetch([{"name": "owner/repo"}])
        reason = run_step(feed, FakeClient(), fetch)
        self.assertIsNone(reason)
        names = [r["name"] for d in feed["days"] for r in d["repos"]]
        self.assertEqual(names, ["Owner/Repo"])
        self.assertEqual(feed_model.problems(feed), [])

    def test_starred_repos_without_forks_are_dropped_before_gemini(self):
        numbers = {"spam/one": (1300, 0), "real/two": (1300, 66), "new/three": (150, 0),
                   "unknown/four": (1300, None)}

        def enrich(repo):
            stars, forks = numbers[repo["name"]]
            return {**repo, "stars": stars, "forks": forks, "readme": "text"}

        feed = feed_model.empty()
        client = FakeClient()
        fetch = fake_fetch([{"name": name} for name in numbers])
        with self.assertLogs("trending_site.job", "INFO") as logs:
            step(feed, client, NOW, fetch_sources=True, fetch=fetch, enrich=enrich)
        self.assertEqual(client.calls, ["real/two", "new/three", "unknown/four"])
        self.assertNotIn("spam/one", [r["name"] for d in feed["days"] for r in d["repos"]])
        self.assertEqual(feed["pending"], [])
        self.assertTrue(any("spam/one" in line for line in logs.output))

    def test_spam_rule_boundaries(self):
        cases = [({"stars": 200, "forks": 0}, True), ({"stars": 199, "forks": 0}, False),
                 ({"stars": 1300}, False), ({"stars": None, "forks": 0}, False)]
        for repo, expected in cases:
            with self.subTest(repo=repo):
                self.assertEqual(looks_like_spam(repo), expected)

    def test_both_sources_failed_still_works_pending_and_fails_status(self):
        feed = feed_model.empty()
        feed["last_fetch"] = "2026-11-10"
        feed["pending"].append(queued("a/old"))
        failed = [{"source": "trendshift", "error": "x"}, {"source": "github", "error": "y"}]
        with self.assertLogs("trending_site.job", "WARNING"):
            reason = run_step(feed, FakeClient(), fake_fetch([], failed))
        self.assertEqual(reason, "Trendshift e GitHub Trending falharam")
        self.assertEqual(feed["last_fetch"], "2026-11-10")
        self.assertEqual(feed["pending"], [])
        self.assertEqual(feed["days"][0]["failed"], failed)
        self.assertEqual([r["name"] for r in feed["days"][0]["repos"]], ["a/old"])

    def test_unavailable_with_nothing_published_fails_status_and_keeps_pending(self):
        feed = feed_model.empty()
        feed["last_fetch"] = TODAY
        feed["pending"] = [queued("a/one"), queued("a/two")]
        client = FakeClient(gemini.Unavailable("daily quota used up"))
        with self.assertLogs("trending_site.pending", "WARNING"):
            reason = run_step(feed, client, fake_fetch([]), fetch_sources=False)
        self.assertEqual(reason, "Gemini indisponível: daily quota used up; 2 na fila")
        self.assertEqual([e["repo"]["name"] for e in feed["pending"]], ["a/one", "a/two"])
        self.assertEqual([e["failures"] for e in feed["pending"]], [0, 0])
        self.assertEqual(feed["days"], [])

    def test_all_refused_fails_status_and_counts(self):
        feed = feed_model.empty()
        feed["last_fetch"] = TODAY
        feed["pending"] = [queued("a/one"), queued("a/two")]
        client = FakeClient(gemini.SummaryFailed("bad schema"))
        with self.assertLogs("trending_site.pending", "WARNING"):
            reason = run_step(feed, client, fake_fetch([]), fetch_sources=False)
        self.assertEqual(reason, "Gemini recusou todos os resumos: bad schema")
        self.assertEqual([e["failures"] for e in feed["pending"]], [1, 1])

    def test_retry_does_not_fetch_and_publishes_without_failed_sources(self):
        feed = feed_model.empty()
        feed["last_fetch"] = TODAY
        feed["pending"] = [queued("a/one")]
        fetch = fake_fetch([{"name": "b/new"}])
        reason = run_step(feed, FakeClient(), fetch, fetch_sources=False)
        self.assertEqual(fetch.calls, [])
        self.assertIsNone(reason)
        self.assertEqual(feed["days"][0]["failed"], [])

    def test_retry_keeps_failed_sources_of_the_day(self):
        feed = feed_model.empty()
        feed["last_fetch"] = TODAY
        gh = {"source": "github", "error": "boom"}
        feed["days"] = [{"date": TODAY, "failed": [gh], "repos": []}]
        feed["pending"] = [queued("a/one")]
        run_step(feed, FakeClient(), fake_fetch([]), fetch_sources=False)
        self.assertEqual(feed["days"][0]["failed"], [gh])
        self.assertEqual([r["name"] for r in feed["days"][0]["repos"]], ["a/one"])

    def test_failed_source_is_recorded_when_nothing_is_published(self):
        feed = feed_model.empty()
        gh = {"source": "github", "error": "boom"}
        down = FakeClient(gemini.Unavailable("down"))
        with self.assertLogs("trending_site", "WARNING"):
            reason = run_step(feed, down, fake_fetch([{"name": "a/one"}], [gh]))
        self.assertEqual(reason, "Gemini indisponível: down; 1 na fila")
        self.assertEqual(feed["days"], [{"date": TODAY, "failed": [gh], "repos": []}])
        self.assertEqual(feed_model.problems(feed), [])
        run_step(feed, FakeClient(), fake_fetch([]), fetch_sources=False)
        self.assertEqual(feed["days"][0]["failed"], [gh])
        self.assertEqual([r["name"] for r in feed["days"][0]["repos"]], ["a/one"])
        self.assertEqual(feed_model.problems(feed), [])

    def test_later_fetch_clears_a_source_that_recovered(self):
        feed = feed_model.empty()
        feed["last_fetch"] = TODAY
        ts = {"source": "trendshift", "error": "boom"}
        feed["days"] = [{"date": TODAY, "failed": [ts], "repos": []}]
        down = FakeClient(gemini.Unavailable("down"))
        with self.assertLogs("trending_site", "INFO") as logs:
            reason = run_step(feed, down, fake_fetch([{"name": "a/one"}]))
        self.assertEqual(reason, "Gemini indisponível: down; 1 na fila")
        self.assertEqual(feed["days"], [{"date": TODAY, "failed": [], "repos": []}])
        self.assertIn(f"updated failed sources on {TODAY}", "\n".join(logs.output))
        self.assertNotIn("published 0 repos", "\n".join(logs.output))
        self.assertEqual(feed_model.problems(feed), [])

    def test_fetch_without_failures_or_repos_adds_no_day(self):
        feed = feed_model.empty()
        reason = run_step(feed, FakeClient(), fake_fetch([]))
        self.assertIsNone(reason)
        self.assertEqual(feed["days"], [])

    def test_daily_cap_counts_repos_already_published_today(self):
        feed = feed_model.empty()
        feed["last_fetch"] = TODAY
        done = [{"name": f"done/{i}", "stars": 1, "summary": None} for i in range(30)]
        feed["days"] = [{"date": TODAY, "failed": [], "repos": done}]
        feed["pending"] = [queued("a/one"), queued("a/two")]
        before = [dict(e) for e in feed["pending"]]
        client = FakeClient()
        reason = run_step(feed, client, fake_fetch([]), fetch_sources=False)
        self.assertIsNone(reason)
        self.assertEqual(client.calls, [])
        self.assertEqual(feed["pending"], before)
        self.assertEqual(len(feed["days"][0]["repos"]), 30)

    def test_daily_cap_publishes_only_what_is_left(self):
        feed = feed_model.empty()
        feed["last_fetch"] = TODAY
        done = [{"name": f"done/{i}", "stars": 1, "summary": None} for i in range(29)]
        feed["days"] = [{"date": TODAY, "failed": [], "repos": done}]
        feed["pending"] = [queued("a/one"), queued("a/two")]
        run_step(feed, FakeClient(), fake_fetch([]), fetch_sources=False)
        self.assertEqual(len(feed["days"][0]["repos"]), 30)
        self.assertEqual([e["repo"]["name"] for e in feed["pending"]], ["a/two"])

    def test_nothing_happening_is_not_a_failure(self):
        feed = feed_model.empty()
        feed["last_fetch"] = TODAY
        reason = run_step(feed, FakeClient(), fake_fetch([]), fetch_sources=False)
        self.assertIsNone(reason)

    def test_prunes_old_days(self):
        feed = feed_model.empty()
        feed["last_fetch"] = TODAY
        feed["days"] = [{"date": "2026-11-01", "failed": [], "repos": []}]
        run_step(feed, FakeClient(), fake_fetch([]), fetch_sources=False)
        self.assertEqual(feed["days"], [])


if __name__ == "__main__":
    unittest.main()
