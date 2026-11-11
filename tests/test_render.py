"""Tests for render.py: template data injection, index data and atomic writes."""

import pathlib
import tempfile
import unittest

from trending_site import render


def repo(name, description="A tool"):
    return {"name": name, "url": f"https://github.com/{name}", "source": "github", "rank": 1,
            "gained": 100, "description": description, "homepage": None, "stars": 100,
            "forks": 1, "issues": 0, "language": "Go", "license": "MIT",
            "created": "2026-11-01", "pushed": "2026-11-03", "image": None, "summary": None}


def page(*repos, failed=(), queued=0):
    return {"date": "2026-11-11", "repos": list(repos), "failed": list(failed), "queued": queued}


class DayHtmlTest(unittest.TestCase):
    def test_hostile_text_cannot_close_the_script(self):
        html = render.day_html(page(repo("a/a", "</script><img src=x onerror=alert(1)>")))
        self.assertEqual(html.count("</script>"), 1)
        self.assertIn('"\\u003c/script>\\u003cimg', html)

    def test_comment_opener_cannot_swallow_the_script_end(self):
        html = render.day_html(page(repo("a/a", "<!--<script> parser")))
        self.assertNotIn("<!--", html)
        self.assertNotIn("<script> parser", html)

    def test_day_page_has_no_days_or_status(self):
        html = render.day_html(page(repo("a/a")))
        self.assertIn('"days": null', html)
        self.assertIn('"status": null', html)


class IndexHtmlTest(unittest.TestCase):
    def test_index_lists_days_and_failed_status(self):
        status = {"ok": False, "at": "2026-11-12T07:00:00-03:00",
                  "reason": "chave do Gemini rejeitada"}
        html = render.index_html(page(repo("a/a")), ["2026-11-11", "2026-11-10"], status)
        for text in ("2026-11-11", "2026-11-10", "chave do Gemini rejeitada"):
            self.assertIn(text, html)

    def test_index_without_days(self):
        self.assertIn('"page": null', render.index_html(None, [], None))


class WriteTest(unittest.TestCase):
    def test_write_replaces_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = pathlib.Path(tmp) / "site"
            render.write(site, "index.html", "a")
            render.write(site, "index.html", "b")
            self.assertEqual((site / "index.html").read_text(), "b")
            self.assertEqual([p.name for p in site.iterdir()], ["index.html"])
            self.assertEqual((site / "index.html").stat().st_mode & 0o777, 0o644)


if __name__ == "__main__":
    unittest.main()
