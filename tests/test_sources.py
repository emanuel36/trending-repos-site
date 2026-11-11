"""Tests for the trending sources and the enrichment of fetched repositories."""

import io
import json
import unittest
import urllib.error
from unittest import mock

from trending_site import sources

REPO_JSON = {
    "stargazers_count": 1200,
    "forks_count": 30,
    "open_issues_count": 4,
    "language": "Rust",
    "license": {"spdx_id": "MIT"},
    "created_at": "2026-10-30T12:00:00Z",
    "pushed_at": "2026-11-03T08:00:00Z",
    "homepage": "https://example.org",
    "description": "A tool",
}


def fake_get(url, headers=None):
    if url.endswith("/readme"):
        return "x" * (sources.README_CHARS + 500)
    return json.dumps(REPO_JSON)


def rate_limited(url, headers=None):
    raise urllib.error.HTTPError(url, 403, "rate limit exceeded", {}, io.BytesIO())


class FirstImageTest(unittest.TestCase):
    def test_first_image_skips_badges(self):
        readme = ("[![CI](https://github.com/o/r/actions/workflows/ci.yml/badge.svg)](x)\n"
                  "![stars](https://img.shields.io/github/stars/o/r)\n"
                  "![demo](docs/demo.gif)")
        self.assertEqual(sources.first_image(readme, "o/r"),
                         "https://raw.githubusercontent.com/o/r/HEAD/docs/demo.gif")

    def test_first_image_reads_html_and_absolute_urls(self):
        readme = '<p><img width="600" src="https://user-images.githubusercontent.com/1/a.png"></p>'
        self.assertEqual(sources.first_image(readme, "o/r"),
                         "https://user-images.githubusercontent.com/1/a.png")

    def test_first_image_rewrites_blob_urls(self):
        readme = "![shot](https://github.com/o/r/blob/main/shot.png)"
        self.assertEqual(sources.first_image(readme, "o/r"),
                         "https://raw.githubusercontent.com/o/r/main/shot.png")

    def test_first_image_skips_star_history_charts(self):
        readme = ("![chart](https://api.star-history.com/chart?repos=o/r)\n"
                  "![shot](shot.png)")
        self.assertEqual(sources.first_image(readme, "o/r"),
                         "https://raw.githubusercontent.com/o/r/HEAD/shot.png")

    def test_first_image_keeps_repos_named_like_badges(self):
        url = "https://raw.githubusercontent.com/dgraph-io/badger/main/shot.png"
        self.assertEqual(sources.first_image(f"![shot]({url})", "dgraph-io/badger"), url)

    def test_first_image_completes_protocol_relative_urls(self):
        self.assertEqual(sources.first_image('<img src="//cdn.example.com/shot.png">', "o/r"),
                         "https://cdn.example.com/shot.png")

    def test_first_image_none_without_images(self):
        self.assertIsNone(sources.first_image("# Title\nText only.", "o/r"))


class EnrichTest(unittest.TestCase):
    @mock.patch.dict("os.environ", {}, clear=True)
    @mock.patch("trending_site.sources.get", side_effect=fake_get)
    def test_enrich_fills_numbers_and_truncates_readme(self, _get):
        repo = sources.enrich({"name": "o/r"})
        self.assertEqual((repo["stars"], repo["forks"], repo["issues"]), (1200, 30, 4))
        self.assertEqual((repo["language"], repo["license"]), ("Rust", "MIT"))
        self.assertEqual((repo["created"], repo["pushed"]), ("2026-10-30", "2026-11-03"))
        self.assertEqual(repo["homepage"], "https://example.org")
        self.assertEqual(len(repo["readme"]), sources.README_CHARS)

    @mock.patch.dict("os.environ", {}, clear=True)
    @mock.patch("trending_site.sources.get", side_effect=rate_limited)
    def test_enrich_fails_when_the_repo_api_is_rate_limited(self, _get):
        with self.assertRaises(sources.EnrichFailed):
            sources.enrich({"name": "o/r", "description": None})

    @mock.patch.dict("os.environ", {}, clear=True)
    def test_enrich_fails_when_the_repo_api_is_down(self):
        def down(url, headers=None):
            if url.endswith("/readme"):
                return "readme"
            raise urllib.error.URLError("connection refused")
        with mock.patch("trending_site.sources.get", side_effect=down), \
                self.assertRaises(sources.EnrichFailed):
            sources.enrich({"name": "o/r"})

    @mock.patch.dict("os.environ", {}, clear=True)
    def test_enrich_keeps_repo_without_readme(self):
        def no_readme(url, headers=None):
            if url.endswith("/readme"):
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())
            return json.dumps(REPO_JSON)
        with mock.patch("trending_site.sources.get", side_effect=no_readme):
            repo = sources.enrich({"name": "o/r", "description": None})
        self.assertEqual(repo["stars"], 1200)
        self.assertIsNone(repo["image"])
        self.assertEqual(repo["readme"], "")

    @mock.patch.dict("os.environ", {"GITHUB_TOKEN": "t"})
    def test_enrich_raises_on_rejected_token(self):
        def unauthorized(url, headers=None):
            raise urllib.error.HTTPError(url, 401, "Bad credentials", {}, io.BytesIO())
        with mock.patch("trending_site.sources.get", side_effect=unauthorized), \
                self.assertRaises(sources.GitHubAuthError):
            sources.enrich({"name": "o/r"})

    @mock.patch.dict("os.environ", {"GITHUB_TOKEN": "t"})
    def test_enrich_sends_the_token(self):
        seen = []
        def record(url, headers=None):
            seen.append(headers["Authorization"])
            return fake_get(url, headers)
        with mock.patch("trending_site.sources.get", side_effect=record):
            sources.enrich({"name": "o/r"})
        self.assertEqual(seen, ["Bearer t", "Bearer t"])


# A row of github.com/trending as served, with the markup the parser must skip over.
TRENDING_ROW = """<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a data-hydro-click="{{&quot;event_type&quot;:&quot;explore.click&quot;}}" href="/{name}"
       data-view-component="true" class="Link"><svg aria-hidden="true" class="octicon">
    <path d="M2 2.5A2.5"></path></svg><span class="text-normal">{owner} /</span> {repo}</a>
  </h2>
  <span class="d-inline-block float-sm-right">{gained}</span>
</article>"""


def trending_row(name, gained):
    owner, repo = name.split("/")
    return TRENDING_ROW.format(name=name, owner=owner, repo=repo, gained=gained)


class GithubTrendingTest(unittest.TestCase):
    def test_reads_names_ranks_and_stars_gained(self):
        page = "<html>" + "".join(trending_row(name, gained) for name, gained in (
            ("a/one", "1,234 stars today"), ("b/two", "1 star today"), ("c/three", "")))
        with mock.patch("trending_site.sources.get", return_value=page):
            self.assertEqual(sources.github_trending(), [
                {"name": "a/one", "source": "github", "rank": 1, "gained": 1234},
                {"name": "b/two", "source": "github", "rank": 2, "gained": 1},
                {"name": "c/three", "source": "github", "rank": 3, "gained": None},
            ])

    def test_ranks_skip_rows_without_a_repository(self):
        page = '<article class="Box-row"><p>ad</p></article>' + trending_row("a/one", "")
        with mock.patch("trending_site.sources.get", return_value=page):
            self.assertEqual(sources.github_trending()[0]["rank"], 1)

    def test_fails_when_page_format_changes(self):
        with mock.patch("trending_site.sources.get", return_value="<html></html>"), \
                self.assertRaises(ValueError):
            sources.github_trending()


class FetchTest(unittest.TestCase):
    def test_skips_known_and_dedups_across_sources_case_insensitively(self):
        with mock.patch("trending_site.sources.trendshift", return_value=[
                {"name": "Foo/Bar", "source": "trendshift", "rank": 1, "gained": 5},
                {"name": "old/one", "source": "trendshift", "rank": 2, "gained": 1}]), \
             mock.patch("trending_site.sources.github_trending", return_value=[
                {"name": "foo/bar", "source": "github", "rank": 1, "gained": 9},
                {"name": "bad name", "source": "github", "rank": 2, "gained": 0}]):
            repos, failed = sources.fetch({"old/one"})
        self.assertEqual([r["name"] for r in repos], ["Foo/Bar"])
        self.assertEqual(failed, [])

    def test_one_source_down_keeps_the_other(self):
        with mock.patch("trending_site.sources.trendshift", side_effect=OSError("blocked")), \
             mock.patch("trending_site.sources.github_trending",
                        return_value=[{"name": "a/b", "source": "github", "rank": 1,
                                       "gained": 1}]):
            repos, failed = sources.fetch(set())
        self.assertEqual([r["name"] for r in repos], ["a/b"])
        self.assertEqual(failed[0]["source"], "trendshift")
        self.assertIn("blocked", failed[0]["error"])
