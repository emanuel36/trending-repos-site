"""Tests for loading and saving the feed."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trending_site import feed as feed_model
from trending_site.store import StoreError, load, save

URL = "https://example.test/data/feed.json"


def fake(status, body=b""):
    seen = []

    def get(url):
        seen.append(url)
        return status, body

    get.seen = seen
    return get


class LoadUrlTest(unittest.TestCase):
    def test_404_raises_unless_missing_is_allowed(self):
        with self.assertRaisesRegex(StoreError, "feed not found \\(404\\)"):
            load(URL, get=fake(404))

    def test_404_is_an_empty_feed_when_missing_is_allowed(self):
        self.assertEqual(load(URL, get=fake(404), allow_missing=True), feed_model.empty())

    def test_url_gets_cache_busting_query(self):
        get = fake(404)
        load(URL, get=get, allow_missing=True)
        self.assertIn("?t=", get.seen[0])

    def test_url_with_query_gets_ampersand(self):
        get = fake(404)
        load(URL + "?a=1", get=get, allow_missing=True)
        self.assertIn("?a=1&t=", get.seen[0])

    def test_valid_feed_is_returned(self):
        body = json.dumps(feed_model.empty()).encode()
        self.assertEqual(load(URL, get=fake(200, body)), feed_model.empty())

    def test_server_error_raises(self):
        with self.assertRaisesRegex(StoreError, "500"):
            load(URL, get=fake(500))

    def test_malformed_feed_raises(self):
        for body in (b'{"days": {}}', b"{"):
            with self.assertRaises(StoreError):
                load(URL, get=fake(200, body))

    def test_invalid_json_names_cause(self):
        with self.assertRaisesRegex(StoreError, "invalid JSON"):
            load(URL, get=fake(200, b"{"))


class LocalTest(unittest.TestCase):
    def test_missing_local_file_raises_unless_missing_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(StoreError, "feed not found"):
                load(str(Path(tmp) / "none.json"))

    def test_missing_local_file_is_empty_when_missing_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "none.json")
            self.assertEqual(load(missing, allow_missing=True), feed_model.empty())

    def test_local_file_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = save(feed_model.empty(), Path(tmp) / "out")
            self.assertEqual(path, Path(tmp) / "out" / "feed.json")
            self.assertEqual(load(str(path)), feed_model.empty())

    def test_invalid_local_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feed.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(StoreError):
                load(str(path))

    def test_save_keeps_non_ascii(self):
        feed = feed_model.empty()
        feed["days"] = [{"date": "2026-11-11", "failed": [],
                         "repos": [{"name": "a/b", "stars": 1, "summary": "生成"}]}]
        with tempfile.TemporaryDirectory() as tmp:
            path = save(feed, Path(tmp))
            self.assertIn("生成", path.read_text(encoding="utf-8"))

    def test_save_replaces_the_file_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = save(feed_model.empty(), Path(tmp))
            old = path.read_text(encoding="utf-8")
            feed = feed_model.empty()
            feed["generated_at"] = "2026-11-11T06:00:00+00:00"
            with mock.patch("trending_site.store.os.replace", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    save(feed, Path(tmp))
            self.assertEqual(path.read_text(encoding="utf-8"), old)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["feed.json"])
            save(feed, Path(tmp))
            self.assertEqual(load(str(path))["generated_at"], feed["generated_at"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["feed.json"])
