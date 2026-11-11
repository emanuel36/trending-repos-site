"""Tests for the daily run."""

import datetime
import json
import os
import pathlib
import shutil
import tempfile
import unittest
import unittest.mock

from trending_site import gemini, run
from trending_site.state import State

SUMMARY = {"what": "Faz algo. Mais.", "who": "Devs", "alternatives": None,
           "maturity": "beta", "translated_from": None}
DAY1 = datetime.datetime(2026, 11, 11, 7, 0, tzinfo=run.TZ)
DAY2 = DAY1 + datetime.timedelta(days=1)
DAY3 = DAY1 + datetime.timedelta(days=2)


def repo(name):
    return {"name": name, "source": "github", "rank": 1, "gained": 10}


def entry(name, queued="2026-11-10"):
    enriched = {**repo(name), "stars": 5, "readme": "r"}
    return {"repo": enriched, "queued": queued, "failures": 0, "last_failure": None}


class FakeClient:
    """Answers per repo name: an exception class to raise, or a summary (the default)."""

    def __init__(self, **outcomes):
        self.outcomes = outcomes
        self.calls = []

    def summarize(self, repo):
        self.calls.append(repo["name"])
        outcome = self.outcomes.get(repo["name"], SUMMARY)
        if isinstance(outcome, type) and issubclass(outcome, Exception):
            raise outcome("scripted")
        return outcome


class RunTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        self.config = run.Config(state_dir=root / "state", site_dir=root / "site")
        self.state = State(self.config.state_dir)
        self.enriched = []

    def fetcher(self, names, failed=()):
        def fetch(known):
            self.known = known
            found = [repo(name) for name in names if name.lower() not in known]
            return found, list(failed)
        return fetch

    def enrich(self, item):
        self.enriched.append(item["name"])
        item.update({"stars": len(self.enriched), "readme": "readme text"})
        return item

    def daily(self, names, client=None, now=DAY1, failed=()):
        return run.run(self.config, client or FakeClient(), now,
                       fetch=self.fetcher(names, failed), enrich=self.enrich)

    def queued_names(self):
        return [item["repo"]["name"] for item in self.state.queue()]

    def test_publishes_summaries_and_marks_seen(self):
        self.assertEqual(self.daily(["a/a", "b/b"]), 0)
        self.assertTrue((self.config.site_dir / "2026-11-11.html").is_file())
        self.assertTrue((self.config.site_dir / "index.html").is_file())
        self.assertEqual(set(self.state.seen()), {"a/a", "b/b"})
        self.assertEqual(self.state.queue(), [])
        self.assertTrue(self.state.status()["ok"])
        page = self.state.day("2026-11-11")
        self.assertEqual([r["name"] for r in page["repos"]], ["b/b", "a/a"])
        self.assertTrue(all("readme" not in r and r["summary"] == SUMMARY for r in page["repos"]))

    def test_today_is_sao_paulo_date(self):
        now = datetime.datetime(2026, 11, 12, 2, 0, tzinfo=datetime.UTC)
        self.assertEqual(run.today(now), "2026-11-11")

    def test_queue_first_then_new(self):
        self.state.save_queue([entry("old/one")])
        client = FakeClient()
        self.assertEqual(self.daily(["new/two"], client), 0)
        self.assertEqual(client.calls, ["old/one", "new/two"])

    def test_known_is_seen_and_queued_lowercased(self):
        self.state.add_seen(["Seen/One"], "2026-11-09")
        self.state.save_queue([entry("Old/One")])
        self.daily([])
        self.assertEqual(self.known, {"seen/one", "old/one"})

    def test_cap_leaves_rest_queued(self):
        self.config.max_repos = 1
        self.assertEqual(self.daily(["a/a", "b/b"]), 0)
        self.assertEqual(set(self.state.seen()), {"a/a"})
        self.assertEqual(self.queued_names(), ["b/b"])
        self.assertEqual(self.state.day("2026-11-11")["queued"], 1)

    def test_quota_publishes_what_it_has(self):
        client = FakeClient(**{"b/b": gemini.Unavailable})
        self.assertEqual(self.daily(["a/a", "b/b", "c/c"], client), 0)
        self.assertEqual(client.calls, ["a/a", "b/b"])
        self.assertEqual(set(self.state.seen()), {"a/a"})
        self.assertEqual(self.queued_names(), ["b/b", "c/c"])
        self.assertEqual(self.state.queue()[0]["failures"], 0)
        self.assertTrue(self.state.status()["ok"])

    def test_gemini_unavailable_with_nothing_published_is_a_failed_run(self):
        client = FakeClient(**{name: gemini.Unavailable for name in ("a/a", "b/b", "c/c")})
        for now, names in ((DAY1, ["a/a", "b/b"]), (DAY2, ["c/c"]), (DAY3, [])):
            self.assertEqual(self.daily(names, client, now), 1)
            status = self.state.status()
            self.assertFalse(status["ok"])
            self.assertEqual(status["at"], now.isoformat())
            self.assertIn("Gemini indisponível: scripted", status["reason"])
        self.assertIn("; 3 na fila", self.state.status()["reason"])
        self.assertEqual(self.queued_names(), ["a/a", "b/b", "c/c"])
        self.assertTrue(all(item["failures"] == 0 for item in self.state.queue()))
        self.assertEqual(self.state.days(), [])
        self.assertEqual(self.state.seen(), {})
        self.assertEqual(sorted(p.name for p in self.config.site_dir.iterdir()), ["index.html"])
        self.assertIn("Gemini indisponível", (self.config.site_dir / "index.html").read_text())

    def test_failure_counts_once_per_day_and_publishes_after_three(self):
        client = FakeClient(**{"a/a": gemini.SummaryFailed})
        self.daily(["a/a"], client, DAY1)
        self.daily(["a/a"], client, DAY1 + datetime.timedelta(hours=1))
        self.assertEqual(self.state.queue()[0]["failures"], 1)
        self.assertEqual(self.state.queue()[0]["last_failure"], "2026-11-11")
        self.assertFalse((self.config.site_dir / "2026-11-11.html").exists())
        self.daily(["a/a"], client, DAY2)
        self.assertEqual(self.state.queue()[0]["failures"], 2)
        self.assertEqual(self.daily(["a/a"], client, DAY3), 0)
        self.assertEqual(self.state.queue(), [])
        page = self.state.day("2026-11-13")
        self.assertEqual([r["name"] for r in page["repos"]], ["a/a"])
        self.assertIsNone(page["repos"][0]["summary"])
        self.assertEqual(self.state.seen(), {"a/a": "2026-11-13"})

    def gemini_client(self, *bad):
        """A real Gemini client whose API answers 400 INVALID_ARGUMENT for the `bad` repos."""
        def post(url, body, headers):
            text = body["contents"][0]["parts"][0]["text"]
            if any(name in text for name in bad):
                return 400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                       "message": "Request contains an invalid argument."}}
            reply = {"content": {"parts": [{"text": json.dumps(SUMMARY)}]}, "finishReason": "STOP"}
            return 200, {"candidates": [reply]}
        return gemini.Client("key", post=post, sleep=lambda seconds: None)

    def test_bad_request_at_the_head_of_the_queue_blames_only_that_repo(self):
        self.state.save_queue([entry("bad/one")])
        self.assertEqual(self.daily(["a/a", "b/b"], self.gemini_client("bad/one")), 0)
        self.assertEqual(set(self.state.seen()), {"a/a", "b/b"})
        self.assertEqual(self.queued_names(), ["bad/one"])
        self.assertEqual(self.state.queue()[0]["failures"], 1)
        self.assertTrue(self.state.status()["ok"])

    def test_gemini_refusing_every_summary_is_a_failed_run(self):
        self.state.save_queue([{**entry("old/one"), "failures": 2, "last_failure": "2026-11-10"}])
        client = self.gemini_client("old/one", "a/a")
        self.assertEqual(self.daily(["a/a"], client), 1)
        status = self.state.status()
        self.assertFalse(status["ok"])
        self.assertTrue(status["reason"].startswith("Gemini recusou todos os resumos: HTTP 400"))
        self.assertEqual(self.queued_names(), ["old/one", "a/a"])
        self.assertEqual([item["failures"] for item in self.state.queue()], [3, 1])
        self.assertEqual(self.state.days(), [])
        self.assertEqual(self.state.seen(), {})
        self.assertEqual(sorted(p.name for p in self.config.site_dir.iterdir()), ["index.html"])

    def test_one_failed_summary_alone_is_not_a_failed_run(self):
        self.assertEqual(self.daily(["a/a"], FakeClient(**{"a/a": gemini.SummaryFailed})), 0)
        self.assertEqual(self.state.queue()[0]["failures"], 1)

    def test_queued_repo_trending_again_is_not_duplicated(self):
        self.state.save_queue([entry("a/a")])
        client = FakeClient(**{"a/a": gemini.Unavailable})
        self.daily(["A/a"], client)
        self.assertEqual(self.queued_names(), ["a/a"])
        self.assertEqual(self.enriched, [])

    def test_both_sources_failed_is_a_failed_run(self):
        failed = [{"source": "trendshift", "error": "x"}, {"source": "github", "error": "y"}]
        self.assertEqual(self.daily([], failed=failed), 1)
        status = self.state.status()
        self.assertFalse(status["ok"])
        self.assertEqual(status["reason"], "Trendshift e GitHub Trending falharam")
        self.assertEqual(status["at"], DAY1.isoformat())
        self.assertFalse((self.config.site_dir / "2026-11-11.html").exists())
        index = (self.config.site_dir / "index.html").read_text()
        self.assertIn("Trendshift e GitHub Trending falharam", index)

    def test_one_source_failed_is_shown_on_the_page(self):
        failed = [{"source": "trendshift", "error": "x"}]
        self.assertEqual(self.daily(["a/a"], failed=failed), 0)
        self.assertEqual(self.state.day("2026-11-11")["failed"], failed)

    def test_rerun_shows_only_sources_that_failed_in_every_run_of_the_day(self):
        trendshift = [{"source": "trendshift", "error": "x"}]
        github = [{"source": "github", "error": "y"}]
        later = DAY1 + datetime.timedelta(hours=1)
        for first, second, expected in ((trendshift, [], []), ([], trendshift, []),
                                        (trendshift, github, []),
                                        (trendshift, trendshift, trendshift)):
            with self.subTest(first=first, second=second):
                shutil.rmtree(self.config.state_dir, ignore_errors=True)
                shutil.rmtree(self.config.site_dir, ignore_errors=True)
                self.assertEqual(self.daily(["a/a"], failed=first), 0)
                self.assertEqual(self.state.day("2026-11-11")["failed"], first)
                self.assertEqual(self.daily(["b/b"], now=later, failed=second), 0)
                self.assertEqual(self.state.day("2026-11-11")["failed"], expected)

    def test_gemini_key_rejected_leaves_pages_untouched(self):
        self.daily(["a/a"])
        day_page = self.config.site_dir / "2026-11-11.html"
        before = day_page.read_bytes()
        reason = "Gemini rejeitou o pedido: HTTP 404 NOT_FOUND"

        class Rejected(gemini.AuthError):
            def __init__(self, _message):
                super().__init__(reason)

        client = FakeClient(**{"c/c": Rejected})
        self.assertEqual(self.daily(["b/b", "c/c"], client, DAY1 + datetime.timedelta(hours=1)), 1)
        self.assertEqual(day_page.read_bytes(), before)
        self.assertEqual(set(self.state.seen()), {"a/a"})
        self.assertEqual(self.queued_names(), ["b/b", "c/c"])
        self.assertEqual([r["name"] for r in self.state.day("2026-11-11")["repos"]], ["a/a"])
        self.assertEqual(self.state.status(),
                         {"ok": False, "at": (DAY1 + datetime.timedelta(hours=1)).isoformat(),
                          "reason": reason})
        self.assertIn(reason, (self.config.site_dir / "index.html").read_text())

    def test_successful_run_renders_the_index_once_with_its_status(self):
        real_write = run.render.write
        written = []

        def write(site_dir, name, html):
            written.append(name)
            real_write(site_dir, name, html)

        with unittest.mock.patch.object(run.render, "write", write):
            self.assertEqual(self.daily(["a/a"]), 0)
        self.assertEqual(written, ["2026-11-11.html", "index.html"])
        self.assertIn(f'"at": "{DAY1.isoformat()}"',
                      (self.config.site_dir / "index.html").read_text())

    def test_seen_only_after_day_page_is_written(self):
        real_write = run.render.write

        def write(site_dir, name, html):
            if name != "index.html":
                raise OSError("disk full")
            real_write(site_dir, name, html)

        with unittest.mock.patch.object(run.render, "write", write):
            self.assertEqual(self.daily(["a/a"]), 1)
        self.assertEqual(self.state.seen(), {})
        self.assertEqual(self.queued_names(), ["a/a"])

    def test_next_run_writes_a_day_page_whose_html_failed(self):
        real_write = run.render.write

        def write(site_dir, name, html):
            if name != "index.html":
                raise OSError("disk full")
            real_write(site_dir, name, html)

        with unittest.mock.patch.object(run.render, "write", write):
            self.assertEqual(self.daily(["a/a"]), 1)
        self.assertEqual(self.daily([], now=DAY2), 0)
        day_page = self.config.site_dir / "2026-11-11.html"
        self.assertIn('"name": "a/a"', day_page.read_text())
        self.assertEqual(self.state.seen(), {"a/a": "2026-11-11"})
        self.assertEqual(self.state.queue(), [])

    def test_day_page_older_than_the_template_is_rewritten(self):
        self.daily(["a/a"])
        day_page = self.config.site_dir / "2026-11-11.html"
        os.utime(self.config.state_dir / "days" / "2026-11-11.json", (1000, 1000))
        os.utime(day_page, (2000, 2000))
        self.assertEqual(self.daily([], now=DAY2), 0)
        self.assertGreaterEqual(day_page.stat().st_mtime, run.render.TEMPLATE.stat().st_mtime)
        self.assertIn('"name": "a/a"', day_page.read_text())

    def test_up_to_date_day_page_is_not_rewritten(self):
        self.daily(["a/a"])
        day_page = self.config.site_dir / "2026-11-11.html"
        before, mtime = day_page.read_bytes(), day_page.stat().st_mtime_ns
        self.assertEqual(self.daily([], now=DAY2), 0)
        self.assertEqual(day_page.read_bytes(), before)
        self.assertEqual(day_page.stat().st_mtime_ns, mtime)

    def test_github_token_rejected_is_a_failed_run(self):
        def enrich(item):
            raise run.sources.GitHubAuthError("401")

        result = run.run(self.config, FakeClient(), DAY1, fetch=self.fetcher(["a/a"]),
                         enrich=enrich)
        self.assertEqual(result, 1)
        self.assertEqual(self.state.status()["reason"], "token do GitHub rejeitado")
        self.assertEqual(self.state.seen(), {})

    def test_repo_github_cannot_enrich_is_skipped_for_today(self):
        def enrich(item):
            if item["name"] == "a/a":
                raise run.sources.EnrichFailed("HTTP 403")
            return self.enrich(item)

        fetch = self.fetcher(["a/a", "b/b"])
        self.assertEqual(run.run(self.config, FakeClient(), DAY1, fetch=fetch, enrich=enrich), 0)
        self.assertEqual(set(self.state.seen()), {"b/b"})
        self.assertEqual(self.state.queue(), [])
        self.assertEqual(self.daily(["a/a"], now=DAY2), 0)
        self.assertEqual(self.state.seen(), {"a/a": "2026-11-12", "b/b": "2026-11-11"})

    def test_unexpected_error_reason_is_truncated_repr(self):
        def fetch(known):
            raise RuntimeError("x" * 500)

        result = run.run(self.config, FakeClient(), DAY1, fetch=fetch, enrich=self.enrich)
        self.assertEqual(result, 1)
        reason = self.state.status()["reason"]
        self.assertEqual(len(reason), 200)
        self.assertTrue(reason.startswith("RuntimeError('xxx"))

    def test_second_run_same_day_adds_to_page(self):
        self.daily(["a/a"])
        self.daily(["b/b"], now=DAY1 + datetime.timedelta(hours=1))
        names = {r["name"] for r in self.state.day("2026-11-11")["repos"]}
        self.assertEqual(names, {"a/a", "b/b"})

    def test_corrupt_state_is_a_failed_run(self):
        self.config.state_dir.mkdir()
        (self.config.state_dir / "seen.json").write_text("{")
        self.assertEqual(self.daily(["a/a"]), 1)
        self.assertFalse((self.config.state_dir / "queue.json").exists())
        self.assertFalse((self.config.state_dir / "days").exists())
        self.assertEqual(self.state.status()["reason"], "estado corrompido: seen.json")
        self.assertFalse((self.config.site_dir / "2026-11-11.html").exists())
        self.assertTrue((self.config.site_dir / "index.html").is_file())

    def test_fail_records_status_and_index(self):
        self.assertEqual(run.fail(self.config, DAY1, "chave do Gemini ausente"), 1)
        self.assertEqual(self.state.status()["reason"], "chave do Gemini ausente")
        self.assertIn("chave do Gemini ausente",
                      (self.config.site_dir / "index.html").read_text())


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        with unittest.mock.patch.dict("os.environ", {}, clear=True):
            config, problem = run.config_from_env()
        self.assertEqual(config, run.Config(pathlib.Path("/var/lib/trending-site"),
                                            pathlib.Path("/srv/trending-site"), 30))
        self.assertIsNone(problem)

    def test_overrides(self):
        env = {"TRENDING_STATE_DIR": "/s", "TRENDING_SITE_DIR": "/h", "TRENDING_MAX_REPOS": "3"}
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            config, problem = run.config_from_env()
        self.assertEqual(config, run.Config(pathlib.Path("/s"), pathlib.Path("/h"), 3))
        self.assertIsNone(problem)

    def test_empty_max_repos_is_the_default(self):
        with unittest.mock.patch.dict("os.environ", {"TRENDING_MAX_REPOS": ""}, clear=True):
            self.assertEqual(run.config_from_env()[0].max_repos, 30)

    def test_invalid_max_repos_keeps_the_dirs(self):
        for value in ("abc", "0", "-2", "3.5"):
            env = {"TRENDING_STATE_DIR": "/s", "TRENDING_SITE_DIR": "/h",
                   "TRENDING_MAX_REPOS": value}
            with self.subTest(value=value), unittest.mock.patch.dict("os.environ", env, clear=True):
                config, problem = run.config_from_env()
                self.assertEqual((config.state_dir, config.site_dir),
                                 (pathlib.Path("/s"), pathlib.Path("/h")))
                self.assertEqual(problem, f"TRENDING_MAX_REPOS inválido: {value}")

if __name__ == "__main__":
    unittest.main()
