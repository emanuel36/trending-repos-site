"""Tests for the pending queue walk."""

import unittest

from trending_site import gemini
from trending_site.pending import MAX_FAILURES, Outcome, work


class FakeClient:
    """Answers by repo name: a dict is a summary, an exception is raised."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    def summarize(self, repo):
        self.calls.append(repo["name"])
        answer = self.script.get(repo["name"], {"pt": repo["name"]})
        if isinstance(answer, Exception):
            raise answer
        return answer


def entry(name, queued="2026-11-01", failures=0, last_failure=None):
    return {"repo": {"name": name}, "queued": queued, "failures": failures,
            "last_failure": last_failure}


class WorkTest(unittest.TestCase):
    def test_publishes_summaries_oldest_first(self):
        entries = [entry("b", "2026-11-03"), entry("a", "2026-11-01"), entry("c", "2026-11-02")]
        client = FakeClient({})
        outcome = work(entries, client, "2026-11-05")
        self.assertEqual(client.calls, ["a", "c", "b"])
        self.assertEqual([r["name"] for r in outcome.published], ["a", "c", "b"])
        self.assertEqual(outcome.published[0], {"name": "a", "summary": {"pt": "a"}})
        self.assertEqual(outcome.kept, [])
        self.assertIsNone(outcome.unavailable)
        self.assertIsNone(outcome.all_refused)
        self.assertEqual([e["repo"]["name"] for e in entries], ["b", "a", "c"])

    def test_limit_keeps_the_rest(self):
        entries = [entry(n, f"2026-11-0{i}") for i, n in enumerate("abcd", 1)]
        client = FakeClient({})
        outcome = work(entries, client, "2026-11-05", limit=2)
        self.assertEqual([r["name"] for r in outcome.published], ["a", "b"])
        self.assertEqual([e["repo"]["name"] for e in outcome.kept], ["c", "d"])
        self.assertEqual(client.calls, ["a", "b"])

    def test_unavailable_stops_without_counting(self):
        entries = [entry("a", "2026-11-01"), entry("b", "2026-11-02"), entry("c", "2026-11-03")]
        client = FakeClient({"b": gemini.Unavailable("x" * 300)})
        outcome = work(entries, client, "2026-11-05")
        self.assertEqual([r["name"] for r in outcome.published], ["a"])
        self.assertEqual([e["repo"]["name"] for e in outcome.kept], ["b", "c"])
        self.assertEqual(outcome.unavailable, "x" * 120)
        self.assertEqual([e["failures"] for e in outcome.kept], [0, 0])
        self.assertEqual(client.calls, ["a", "b"])
        self.assertIsNone(outcome.all_refused)

    def test_failure_counts_once_per_day_and_publishes_after_three(self):
        item = entry("a")
        client = FakeClient({"a": gemini.SummaryFailed("bad")})
        first = work([item], client, "2026-11-05")
        self.assertEqual(first.kept[0]["failures"], 1)
        again = work(first.kept, client, "2026-11-05")
        self.assertEqual(again.kept[0]["failures"], 1)
        self.assertEqual(again.kept[0]["last_failure"], "2026-11-05")
        second = work(again.kept, client, "2026-11-06")
        self.assertEqual(second.kept[0]["failures"], 2)
        third = work(second.kept, client, "2026-11-07")
        self.assertEqual(MAX_FAILURES, 3)
        self.assertEqual(third.published, [{"name": "a", "summary": None}])
        self.assertEqual(third.kept, [])
        self.assertIsNone(third.all_refused)

    def test_all_refused_keeps_counts_and_publishes_only_the_exhausted(self):
        entries = [entry("a", failures=2, last_failure="2026-11-04"), entry("b")]
        client = FakeClient({"a": gemini.SummaryFailed("no"), "b": gemini.SummaryFailed("nope")})
        outcome = work(entries, client, "2026-11-05")
        self.assertEqual(outcome.published, [{"name": "a", "summary": None}])
        self.assertEqual(outcome.all_refused, "nope")
        self.assertEqual([e["repo"]["name"] for e in outcome.kept], ["b"])
        self.assertEqual([e["failures"] for e in outcome.kept], [1])

    def test_all_refused_still_publishes_entries_at_max_failures(self):
        entries = [entry("a", failures=2, last_failure="2026-11-04"),
                   entry("b", "2026-11-02", failures=2, last_failure="2026-11-04")]
        client = FakeClient({"a": gemini.SummaryFailed("no"), "b": gemini.SummaryFailed("nope")})
        outcome = work(entries, client, "2026-11-05")
        self.assertEqual(outcome.published,
                         [{"name": "a", "summary": None}, {"name": "b", "summary": None}])
        self.assertEqual(outcome.kept, [])
        self.assertEqual(outcome.all_refused, "nope")

    def test_one_refusal_among_successes_is_not_all_refused(self):
        entries = [entry("a", "2026-11-01"), entry("b", "2026-11-02")]
        client = FakeClient({"a": gemini.SummaryFailed("no")})
        outcome = work(entries, client, "2026-11-05")
        self.assertIsNone(outcome.all_refused)
        self.assertEqual([r["name"] for r in outcome.published], ["b"])
        self.assertEqual([e["repo"]["name"] for e in outcome.kept], ["a"])

    def test_single_refusal_is_not_all_refused(self):
        outcome = work([entry("a")], FakeClient({"a": gemini.SummaryFailed("no")}), "2026-11-05")
        self.assertIsNone(outcome.all_refused)
        self.assertIsInstance(outcome, Outcome)

    def test_other_errors_propagate(self):
        client = FakeClient({"a": gemini.AuthError("key")})
        with self.assertRaises(gemini.AuthError):
            work([entry("a")], client, "2026-11-05")
