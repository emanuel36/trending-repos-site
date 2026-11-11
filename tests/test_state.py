"""Tests for the state store."""

import pathlib
import tempfile
import unittest

from trending_site.state import State, StateError


def entry(name):
    return {"repo": {"name": name, "readme": "x"}, "queued": "2026-11-09", "failures": 0,
            "last_failure": None}


def page(date, *names):
    return {"date": date, "repos": [{"name": n} for n in names], "failed": [], "queued": 0}


class StateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.state = State(self.root)

    def test_missing_files_are_empty(self):
        self.assertEqual(self.state.seen(), {})
        self.assertEqual(self.state.queue(), [])
        self.assertEqual(self.state.days(), [])
        self.assertIsNone(self.state.status())

    def test_corrupt_seen_raises(self):
        (self.root / "seen.json").write_text("{")
        with self.assertRaises(StateError) as ctx:
            self.state.seen()
        self.assertIn("seen.json", str(ctx.exception))

    def test_undecodable_seen_raises(self):
        (self.root / "seen.json").write_bytes(b"\xff")
        with self.assertRaises(StateError) as ctx:
            self.state.seen()
        self.assertIn("seen.json", str(ctx.exception))

    def test_files_are_private(self):
        self.state.add_seen(["a/a"], "2026-11-10")
        self.state.save_day(page("2026-11-10", "a/a"))
        for path in (self.root / "seen.json", self.root / "days" / "2026-11-10.json"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_add_seen_keeps_first_day(self):
        self.state.add_seen(["a/a"], "2026-11-10")
        self.state.add_seen(["a/a", "b/b"], "2026-11-11")
        self.assertEqual(self.state.seen(), {"a/a": "2026-11-10", "b/b": "2026-11-11"})

    def test_days_newest_first(self):
        self.state.save_day(page("2026-11-09"))
        self.state.save_day(page("2026-11-11"))
        self.assertEqual(self.state.days(), ["2026-11-11", "2026-11-09"])
        self.assertEqual(self.state.day("2026-11-09")["date"], "2026-11-09")
        self.assertIsNone(self.state.day("2026-01-01"))

    def test_reconcile_marks_published_and_drops_from_queue(self):
        self.state.save_queue([entry("A/a"), entry("b/b")])
        self.state.save_day(page("2026-11-10", "a/a"))
        self.state.reconcile()
        self.assertEqual(self.state.seen(), {"a/a": "2026-11-10"})
        self.assertEqual([e["repo"]["name"] for e in self.state.queue()], ["b/b"])

    def test_reconcile_keeps_earliest_page_and_existing_case(self):
        self.state.add_seen(["Foo/Bar"], "2026-11-08")
        self.state.save_day(page("2026-11-10", "foo/bar"))
        self.state.save_day(page("2026-11-09", "foo/bar"))
        self.state.reconcile()
        self.assertEqual(self.state.seen(), {"Foo/Bar": "2026-11-08"})

    def test_status_roundtrip(self):
        self.state.save_status(False, "2026-11-11T07:00:00", "boom")
        self.assertEqual(self.state.status(),
                         {"ok": False, "at": "2026-11-11T07:00:00", "reason": "boom"})

    def test_corrupt_day_raises(self):
        self.state.save_day(page("2026-11-10"))
        (self.root / "days" / "2026-11-10.json").write_text("nope")
        with self.assertRaises(StateError):
            self.state.reconcile()

    def test_write_leaves_no_temp_files(self):
        self.state.save_queue([])
        self.assertEqual([p.name for p in self.root.iterdir()], ["queue.json"])


if __name__ == "__main__":
    unittest.main()
