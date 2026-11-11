"""Tests for the feed model."""

import unittest

from trending_site import feed as feed_model
from trending_site.feed import (
    add_pending,
    empty,
    known_names,
    problems,
    prune,
    publish,
    set_status,
)


def day(date, *names, failed=()):
    return {"date": date, "failed": list(failed),
            "repos": [{"name": n, "stars": 1, "summary": None} for n in names]}


class FeedTest(unittest.TestCase):
    def test_empty_has_no_problems(self):
        self.assertEqual(problems(empty()), [])
        self.assertEqual(empty()["version"], 1)

    def test_problems_reject_bad_shapes(self):
        bad = [[], {}, {**empty(), "days": {}}, {**empty(), "pending": [{"repo": {}}]},
               {**empty(), "days": [{"failed": [], "repos": []}]}]
        for entry in bad:
            with self.subTest(entry=entry):
                self.assertTrue(problems(entry))

    def test_problems_reject_invalid_content(self):
        repo = {"name": "a/b", "stars": 1, "summary": None}
        pend = {"repo": {"name": "p/q", "stars": None}, "queued": "2026-11-11",
                "failures": 0, "last_failure": None}
        d = day("2026-11-11", "a/b")
        bad = {
            "version true": {**empty(), "version": True},
            "generated_at": {**empty(), "generated_at": 3},
            "last_fetch": {**empty(), "last_fetch": "11/11/2026"},
            "status ok": {**empty(), "status": {"ok": 1, "at": "x", "reason": None}},
            "status reason": {**empty(), "status": {"ok": True, "at": "x", "reason": 2}},
            "status type": {**empty(), "status": []},
            "bad date": {**empty(), "days": [day("2026-13-40")]},
            "failed entry": {**empty(), "days": [{**d, "failed": [{"source": "x"}]}]},
            "repo name": {**empty(), "days": [{**d, "repos": [{**repo, "name": 1}]}]},
            "stars bool": {**empty(), "days": [{**d, "repos": [{**repo, "stars": True}]}]},
            "summary": {**empty(), "days": [{**d, "repos": [{**repo, "summary": "x"}]}]},
            "readme": {**empty(), "days": [{**d, "repos": [{**repo, "readme": "x"}]}]},
            "duplicate dates": {**empty(), "days": [d, day("2026-11-11", "c/d")]},
            "oldest first": {**empty(), "days": [day("2026-11-10"), day("2026-11-11")]},
            "pending repo": {**empty(), "pending": [{**pend, "repo": {"stars": 1}}]},
            "pending queued": {**empty(), "pending": [{**pend, "queued": "x"}]},
            "failures bool": {**empty(), "pending": [{**pend, "failures": True}]},
            "failures negative": {**empty(), "pending": [{**pend, "failures": -1}]},
            "last_failure": {**empty(), "pending": [{**pend, "last_failure": "x"}]},
            "dup across days": {**empty(), "days": [d, day("2026-11-10", "A/B")]},
            "dup with pending": {**empty(), "days": [d],
                                 "pending": [{**pend, "repo": {"name": "A/b", "stars": 1}}]},
        }
        for label, feed in bad.items():
            with self.subTest(label=label):
                self.assertTrue(problems(feed))
        good = {**empty(), "days": [d], "pending": [pend], "last_fetch": "2026-11-11",
                "status": {"ok": True, "at": "x", "reason": None}}
        self.assertEqual(problems(good), [])

    def test_known_names_are_lowercase_across_days_and_pending(self):
        feed = {**empty(), "days": [day("2026-11-11", "Foo/Bar")],
                "pending": [{"repo": {"name": "A/B"}, "queued": "2026-11-11",
                             "failures": 0, "last_failure": None}]}
        self.assertEqual(known_names(feed), {"foo/bar", "a/b"})

    def test_add_pending_skips_known_case_insensitively(self):
        feed = {**empty(), "days": [day("2026-11-11", "Foo/Bar")]}
        add_pending(feed, [{"name": "foo/bar"}, {"name": "a/b"}], "2026-11-11")
        self.assertEqual([e["repo"]["name"] for e in feed["pending"]], ["a/b"])
        self.assertEqual(feed["pending"][0]["failures"], 0)

    def test_publish_creates_day_drops_readme_and_sorts_by_stars(self):
        feed = {**empty(), "days": [day("2026-11-10", "old/one")]}
        repos = [{"name": "a/none", "stars": None, "readme": "x", "summary": None},
                 {"name": "a/low", "stars": 5, "readme": "x", "summary": None},
                 {"name": "a/high", "stars": 50, "readme": "x", "summary": None}]
        publish(feed, repos, "2026-11-11", [{"source": "github", "error": "e"}])
        self.assertEqual([d["date"] for d in feed["days"]], ["2026-11-11", "2026-11-10"])
        new = feed["days"][0]
        self.assertEqual([r["name"] for r in new["repos"]], ["a/high", "a/low", "a/none"])
        self.assertTrue(all("readme" not in r for r in new["repos"]))
        self.assertEqual(new["failed"], [{"source": "github", "error": "e"}])
        self.assertEqual(problems(feed), [])

    def test_publish_same_day_keeps_earlier_repos_and_intersects_failed(self):
        feed = empty()
        ts = {"source": "trendshift", "error": "e"}
        gh = {"source": "github", "error": "e"}
        publish(feed, [{"name": "a/one", "stars": 1, "summary": None}], "2026-11-11", [ts])
        publish(feed, [{"name": "a/two", "stars": 2, "summary": None}], "2026-11-11", [gh])
        self.assertEqual(len(feed["days"]), 1)
        self.assertEqual(feed["days"][0]["failed"], [])
        self.assertEqual([r["name"] for r in feed["days"][0]["repos"]], ["a/two", "a/one"])

    def test_publish_without_fetch_keeps_existing_failed(self):
        feed = empty()
        ts = {"source": "trendshift", "error": "e"}
        publish(feed, [], "2026-11-11", [ts])
        publish(feed, [{"name": "a/one", "stars": 1, "summary": None}], "2026-11-11", None)
        self.assertEqual(feed["days"][0]["failed"], [ts])
        self.assertEqual([r["name"] for r in feed["days"][0]["repos"]], ["a/one"])
        self.assertEqual(problems(feed), [])

    def test_publish_without_fetch_on_new_day_has_no_failed(self):
        feed = empty()
        publish(feed, [{"name": "a/one", "stars": 1, "summary": None}], "2026-11-11", None)
        self.assertEqual(feed["days"][0]["failed"], [])

    def test_publish_day_with_no_repos_is_valid(self):
        feed = empty()
        publish(feed, [], "2026-11-11", [{"source": "github", "error": "e"}])
        self.assertEqual(feed["days"][0]["repos"], [])
        self.assertEqual(problems(feed), [])

    def test_publish_same_repo_twice_replaces_it(self):
        feed = empty()
        publish(feed, [{"name": "A/One", "stars": 1, "summary": None}], "2026-11-11", [])
        publish(feed, [{"name": "a/one", "stars": 9, "summary": None}], "2026-11-11", [])
        self.assertEqual([(r["name"], r["stars"]) for r in feed["days"][0]["repos"]],
                         [("a/one", 9)])

    def test_prune_keeps_seven_calendar_days(self):
        dates = [f"2026-11-{d:02d}" for d in (11, 10, 9, 8, 7, 6, 5, 4)]
        feed = {**empty(), "days": [day(d) for d in dates]}
        prune(feed, "2026-11-11")
        self.assertEqual([d["date"] for d in feed["days"]],
                         [f"2026-11-{n:02d}" for n in (11, 10, 9, 8, 7, 6, 5)])
        self.assertNotIn("2026-11-04", [d["date"] for d in feed["days"]])
        self.assertEqual(feed_model.WINDOW_DAYS, 7)

    def test_set_status(self):
        feed = empty()
        set_status(feed, False, "2026-11-11T06:00:00Z", "falha")
        self.assertEqual(feed["status"],
                         {"ok": False, "at": "2026-11-11T06:00:00Z", "reason": "falha"})
