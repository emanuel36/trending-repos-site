# Trending Repos on GitHub Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the VM build with a GitHub Actions job that keeps a 7-day public feed on
GitHub Pages, and a static page that shows each visitor only what their browser has not seen.

**Architecture:** The Python job downloads the published `data/feed.json`, fetches and
summarizes new repos, updates the feed with pure functions, and writes it into `site/data/`; the
workflow deploys `site/` to Pages. The page (`site/`) is static: it loads the feed and keeps the
visitor's history in `localStorage`. Nothing the job produces is committed.

**Tech Stack:** Python 3.12 standard library, Gemini REST `generateContent`, vanilla JS (ES
modules), Node's built-in test runner for the page logic, GitHub Actions + Pages.

**Spec:** `docs/superpowers/specs/2026-11-11-trending-repos-pages-design.md`

## Global Constraints

- Python 3.12 standard library only at runtime; no third-party JS; no build step for the page.
- 100-char lines, absolute imports, Google-style docstrings on non-trivial public APIs, ruff
  clean (`uvx ruff check .`), pytest green (`uvx --with pytest pytest`).
- Page tests: `node --test tests/js/` (Node 20+; built-in runner, no packages).
- Window: last 7 calendar days, São Paulo dates, today included; at most 30 repos published per
  day; a repo's own failure counts once per day, published description-only after 3 days.
- README capped at 8000 chars in `pending`; never in `days`.
- Schedules (UTC): `0 6 * * *` run (03:00 São Paulo); `0 9 * * *`, `0 12 * * *`, `0 15 * * *`
  retry (06:00, 09:00, 12:00 São Paulo).
- Feed URL: `https://emanuel36.github.io/trending-repos-site/data/feed.json`.
- `localStorage` key: `trending-repos-site:seen:v1`.
- Status reasons in Portuguese; page copy in Portuguese; code, docs and commits in English.
- Secrets: `GEMINI_API_KEY` (and optional `GEMINI_MODEL`) as repository secrets; `GITHUB_TOKEN`
  from the workflow; the key never in logs.
- Nothing under `~/.dotfiles/claude/skills/trending-repos` is modified.
- Commits: Conventional Commits, subject ≤50 chars, trailer
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; never push.

## Review Focus

- **Stale feed from the Pages CDN** right after a deploy: the job must request the feed with a
  cache-busting query, and runs never overlap (workflow concurrency). Test in Task 3.
- **Feed downloaded but malformed** (truncated, wrong types): no publish, exit 1, nothing
  overwritten. Test in Task 3.
- **Same repo under different case** in `days` vs a new fetch: listed once. Test in Task 2.
- **History entry for a repo that left the window** and trends again: still hidden for that
  browser. Test in Task 6.
- **`localStorage` throws** (private window, blocked): page renders the latest day, no crash.
  Test in Task 6.

## File Structure

```
trending_site/__init__.py
trending_site/__main__.py     CLI: run | retry, --feed URL-or-path, --out DIR
trending_site/sources.py      kept as is
trending_site/summary.py      kept as is
trending_site/gemini.py       kept as is
trending_site/feed.py         pure feed model: empty, validate, known names, add, publish, prune
trending_site/pending.py      walk of the pending queue through Gemini (moved from run.py)
trending_site/store.py        load the published feed, save the new one
trending_site/job.py          one run or retry: orchestration, status, São Paulo date
site/index.html               static page shell
site/style.css                styles from the current template
site/history.js               pure history logic (ES module)
site/app.js                   loads the feed, renders, saves history
tests/test_sources.py, test_summary.py, test_gemini.py   kept
tests/test_feed.py, test_pending.py, test_store.py, test_job.py, test_main.py
tests/js/history.test.mjs
.github/workflows/daily.yml
README.md                     rewritten
```

Removed: `trending_site/{state,render,run,files}.py`, `trending_site/assets/`, `deploy/`,
`tests/{test_state,test_render,test_run,test_files,test_main}.py` (test_main is rewritten).

Feed shape (JSON, written with `ensure_ascii=False`):

```
{"version": 1, "generated_at": ISO datetime, "last_fetch": "YYYY-MM-DD" | null,
 "status": {"ok": bool, "at": ISO datetime, "reason": str | null} | null,
 "days": [{"date": "YYYY-MM-DD", "failed": [{"source", "error"}], "repos": [repo]}],
 "pending": [{"repo": repo-with-readme, "queued": date, "failures": int,
              "last_failure": date | null}]}
```

A `repo` has the fields `sources.enrich` produces plus `summary` (dict or null) in `days`.

---

### Task 1: Remove the VM build

**Files:**
- Delete: `trending_site/state.py`, `render.py`, `run.py`, `files.py`, `assets/`, `deploy/`,
  `trending_site/__main__.py`, `tests/test_state.py`, `test_render.py`, `test_run.py`,
  `test_files.py`, `test_main.py`

- [ ] **Step 1:** Delete the files. `pyproject.toml` stays.
- [ ] **Step 2:** `uvx --with pytest pytest` → only sources, summary and gemini tests, all PASS;
  `uvx ruff check .` clean; `rg -n "import .*\b(state|render|run|files)\b" trending_site tests`
  finds nothing.
- [ ] **Step 3: Commit** `refactor: drop the VM-era state and rendering`

### Task 2: Feed model

**Files:**
- Create: `trending_site/feed.py`
- Test: `tests/test_feed.py`

**Interfaces:**
- Produces:
  - `WINDOW_DAYS = 7`
  - `empty() -> dict` — `{"version": 1, "generated_at": None, "last_fetch": None,
    "status": None, "days": [], "pending": []}`
  - `problems(feed: object) -> list[str]` — empty when the shape above holds (types of every
    top-level key, each day's `date`/`failed`/`repos`, each pending entry's keys)
  - `known_names(feed: dict) -> set[str]` — lowercase names in `days` and `pending`
  - `add_pending(feed: dict, repos: list[dict], day: str) -> None` — appends entries
    `{"repo", "queued": day, "failures": 0, "last_failure": None}`, skipping known names
  - `publish(feed: dict, repos: list[dict], day: str, failed: list[dict]) -> None` — adds repos
    (README dropped) to the day `day`, creating it at the front if missing; a day keeps repos
    from earlier runs; repos sorted by stars, unknown last; `failed` of an existing day becomes
    the intersection by `source` with the new list, a new day takes the new list
  - `prune(feed: dict, day: str) -> None` — drops days with `date` earlier than
    `day - (WINDOW_DAYS - 1)`; keeps days newest first
  - `set_status(feed: dict, ok: bool, at: str, reason: str | None) -> None`

- [ ] **Step 1: Write the failing tests** in `tests/test_feed.py` (unittest style, as the kept
  tests):
  - `test_empty_has_no_problems`: `problems(empty()) == []`.
  - `test_problems_reject_bad_shapes`: subTests over `[]`, `{}`, `{**empty(), "days": {}}`,
    `{**empty(), "pending": [{"repo": {}}]}`, a day without `date` → each non-empty.
  - `test_known_names_are_lowercase_across_days_and_pending`.
  - `test_add_pending_skips_known_case_insensitively`: feed day holds `Foo/Bar`; adding
    `foo/bar` and `a/b` → pending names `["a/b"]`.
  - `test_publish_creates_day_drops_readme_and_sorts_by_stars`.
  - `test_publish_same_day_keeps_earlier_repos_and_intersects_failed`: first publish with
    `failed=[trendshift]`, second with `failed=[github]` → `failed == []`, both repos kept.
  - `test_prune_keeps_seven_calendar_days`: days `2026-11-05`…`2026-11-11` plus `2026-11-04`,
    `prune(feed, "2026-11-11")` → oldest kept is `2026-11-05`.
  - `test_set_status`.
- [ ] **Step 2: Run** `uvx --with pytest pytest tests/test_feed.py -v` → FAIL (ImportError).
- [ ] **Step 3: Implement `trending_site/feed.py`.** Dates compare with `datetime.date`.
- [ ] **Step 4: Run** → PASS; ruff clean.
- [ ] **Step 5: Commit** `feat: add the feed model`

### Task 3: Load and save the feed

**Files:**
- Create: `trending_site/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `feed.empty`, `feed.problems`
- Produces:
  - `class StoreError(Exception)` — the feed could not be read or is invalid; never publish
  - `load(source: str, get=None) -> dict` — `source` is an `http(s)` URL or a local path. URL:
    GET with query `?t=<unix seconds>` appended, timeout 30 s, `get(url) -> tuple[int, bytes]`
    injectable (default `urllib`); 404 → `feed.empty()`; other status, network error, invalid
    JSON or `feed.problems()` → `StoreError` naming the cause. Path: missing file →
    `feed.empty()`; unreadable/invalid → `StoreError`.
  - `save(feed: dict, out_dir: pathlib.Path) -> pathlib.Path` — writes
    `out_dir/feed.json` (creates the dir), compact JSON, `ensure_ascii=False`; returns the path

- [ ] **Step 1: Write the failing tests:**
  - `test_404_is_an_empty_feed`.
  - `test_url_gets_cache_busting_query`: the fake `get` records a URL containing `?t=`.
  - `test_server_error_raises`: 500 → `StoreError`.
  - `test_malformed_feed_raises`: 200 with `b'{"days": {}}'` and with `b'{'` → `StoreError`.
  - `test_missing_local_file_is_empty` / `test_local_file_round_trip` with `save`.
  - `test_save_keeps_non_ascii`: a summary `"生成"` appears verbatim in the file.
- [ ] **Step 2: Run** `uvx --with pytest pytest tests/test_store.py -v` → FAIL.
- [ ] **Step 3: Implement `trending_site/store.py`.**
- [ ] **Step 4: Run** → PASS; ruff clean.
- [ ] **Step 5: Commit** `feat: load and save the published feed`

### Task 4: Pending queue through Gemini

**Files:**
- Create: `trending_site/pending.py`
- Test: `tests/test_pending.py`

**Interfaces:**
- Consumes: `gemini.Unavailable`, `gemini.SummaryFailed` (from `trending_site/gemini.py`),
  `client.summarize(repo: dict) -> dict`
- Produces:
  - `MAX_FAILURES = 3`, `MAX_PER_DAY = 30`, `REASON_CHARS = 120`
  - `@dataclass Outcome: published: list[dict]; kept: list[dict]; unavailable: str | None;
    all_refused: str | None`
  - `work(entries: list[dict], client, day: str, limit: int = MAX_PER_DAY) -> Outcome` — the walk
    of the old `run._publish` lines 201-237: oldest first; stop at `limit` published (rest kept);
    `Unavailable` → stop, rest kept, counts untouched, `unavailable = str(error)[:120]`;
    `SummaryFailed` → count once per `day` via `last_failure`, publish with `summary: None` at
    `MAX_FAILURES`, else keep; `all_refused` set when at least 2 entries were tried and every one
    raised `SummaryFailed` — then `published` is empty and every entry is kept with its new count.
    Published items are `{**entry["repo"], "summary": result}`. Other exceptions propagate.

- [ ] **Step 1: Write the failing tests** with a scripted fake client (by repo name):
  - `test_publishes_summaries_oldest_first` (call order recorded).
  - `test_limit_keeps_the_rest`.
  - `test_unavailable_stops_without_counting`.
  - `test_failure_counts_once_per_day_and_publishes_after_three` (same day twice → 1; days 2
    and 3 → published with `summary is None`).
  - `test_all_refused_publishes_nothing_but_keeps_counts` (two entries, both refused).
  - `test_one_refusal_among_successes_is_not_all_refused`.
- [ ] **Step 2: Run** `uvx --with pytest pytest tests/test_pending.py -v` → FAIL.
- [ ] **Step 3: Implement `trending_site/pending.py`.**
- [ ] **Step 4: Run** → PASS; ruff clean.
- [ ] **Step 5: Commit** `feat: work the pending queue through Gemini`

### Task 5: Job and CLI

**Files:**
- Create: `trending_site/job.py`, `trending_site/__main__.py`
- Test: `tests/test_job.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `feed.*`, `store.load/save/StoreError`, `pending.work/Outcome`,
  `sources.fetch(known) -> (repos, failed)`, `sources.enrich(repo) -> dict`,
  `sources.GitHubAuthError`, `sources.EnrichFailed`, `gemini.AuthError`, `gemini.Client`.
- Produces:
  - `TZ = zoneinfo.ZoneInfo("America/Sao_Paulo")`; `today(now: datetime) -> str`
  - `step(feed: dict, client, now: datetime, *, fetch_sources: bool, fetch=sources.fetch,
    enrich=sources.enrich) -> tuple[bool, str | None]` — mutates `feed`; returns
    `(changed, failure_reason)`.
  - `__main__.main(argv: list[str] | None = None) -> int` with `run|retry`, `--feed SOURCE`
    (default the Feed URL constant), `--out DIR` (default `site/data`).

`step` does, in order:
1. If `fetch_sources`: `fetch(feed.known_names(feed))`; log each failed source; enrich each repo
   (`EnrichFailed` → skip with a log line), `feed.add_pending`, set `last_fetch = today`. Both
   sources failed → failure reason "Trendshift e GitHub Trending falharam" (pending is still
   worked).
2. `pending.work(feed["pending"], client, today)`; `feed["pending"] = outcome.kept`; when
   `outcome.published`, `feed.publish(..., failed)` (failed = this run's list, or `[]` in retry).
3. Failure reasons, first match wins: both sources failed; `outcome.all_refused` →
   "Gemini recusou todos os resumos: <msg>"; `outcome.unavailable` and nothing published →
   "Gemini indisponível: <msg>; N na fila".
4. `feed.prune(feed, today)`; `changed` is true when anything was fetched, published, counted,
   or a failure is recorded.

`main` does:
1. Load the feed (`StoreError` → log, return 1, write nothing).
2. Decide mode: `retry` with `last_fetch != today` → behaves as `run`. `retry` with empty
   `pending` and `last_fetch == today` → log "nothing to do", return 0, write nothing.
3. Missing `GEMINI_API_KEY` → "chave do Gemini ausente"; missing `GITHUB_TOKEN` when fetching →
   "token do GitHub ausente"; `sources.GitHubAuthError` → "token do GitHub rejeitado";
   `gemini.AuthError` → `str(error)`; any other exception → `repr(error)[:200]`. Each of these
   is a failed status.
4. `feed.set_status(ok, now.isoformat(), reason)`, `generated_at = now.isoformat()`,
   `store.save(feed, out)`; return 1 on failure, else 0. A failed run still saves, so the
   workflow publishes the status.

- [ ] **Step 1: Write the failing tests** (fake fetch/enrich/client, temp `--out`, local feed
  file as `--feed`):
  - `test_today_is_sao_paulo_date`: 02:00 UTC on 2026-11-12 → `"2026-11-11"`.
  - `test_run_publishes_and_records_last_fetch`.
  - `test_queued_repo_trending_again_is_not_duplicated` (case differs).
  - `test_both_sources_failed_still_works_pending_and_fails_status`.
  - `test_unavailable_with_nothing_published_fails_status_and_keeps_pending`.
  - `test_retry_without_fetch_today_does_full_run`.
  - `test_retry_with_empty_pending_writes_nothing` (no `feed.json` in `--out`, exit 0).
  - `test_invalid_feed_writes_nothing` (exit 1, no file).
  - `test_missing_key_saves_failed_status` (exit 1, file written, `status.ok` false).
  - `test_gemini_auth_error_reason_is_its_message`.
- [ ] **Step 2: Run** `uvx --with pytest pytest tests/test_job.py tests/test_main.py -v` → FAIL.
- [ ] **Step 3: Implement** `job.py` and `__main__.py`; `logging` to stderr, never the key.
- [ ] **Step 4: Run** the full suite → PASS; ruff clean.
- [ ] **Step 5: Local smoke** without Gemini: `python3 -m trending_site run --feed
  <scratch>/feed.json --out <scratch>/out` with `GITHUB_TOKEN` unset → exit 1, `feed.json`
  written with status "token do GitHub ausente"; no network call before the check.
- [ ] **Step 6: Commit** `feat: add the daily job and CLI`

### Task 6: Static page and browser history

**Files:**
- Create: `site/index.html`, `site/style.css`, `site/history.js`, `site/app.js`
- Test: `tests/js/history.test.mjs`

**Interfaces:**
- Consumes: the feed shape above, served at `data/feed.json` relative to the page.
- Produces (`site/history.js`, ES module):
  - `KEY = "trending-repos-site:seen:v1"`, `PAST = "1970-01-01"`
  - `load(storage) -> object` — `{lowercase name: "YYYY-MM-DD"}`; missing, corrupt or throwing
    storage → `{}`
  - `save(storage, history) -> void` — swallows storage errors
  - `select(days, history, today) -> {shown: [{date, repos}], history}` — `days` newest first;
    empty `history` → `shown` holds only `days[0]`, its repos recorded with `today`, every other
    repo in `days` recorded with `PAST`; otherwise each day keeps repos whose name is absent from
    `history` or recorded with `today`, days left empty are dropped, new names recorded with
    `today`; never mutates its inputs
  - `localDate(date: Date) -> "YYYY-MM-DD"` in the visitor's time zone

Page behavior (`app.js`, ported from the current `trending_site/assets/page.html` on `master`):
- Same look: copy its CSS into `style.css`, its `esc`, `short`, `day`, `safeUrl`, `thumb`,
  `tags`, `row` helpers into `app.js`. Rows grouped under a heading per day ("Hoje · 11 de
  novembro" for today, otherwise "10 de novembro"); first row of the first group open.
- Header: title "Em alta", a sub line with the shown count, per-source counts, "<fonte> falhou"
  for the latest day's `failed`, "N aguardando resumo" when `pending.length > 0`; a banner
  "última execução falhou em DD/MM: <motivo>" when `status.ok` is false.
- Empty `shown` (only possible with a non-empty history and nothing new or seen today): "Nada
  novo hoje." Feed fetch failure: "Não foi possível carregar os dados."
- Fetch `data/feed.json` with `cache: "no-store"`.
- `index.html` has `<meta name="robots" content="noindex">` and loads `app.js` as a module.

- [ ] **Step 1: Write the failing tests** in `tests/js/history.test.mjs` with `node:test` and
  `node:assert`:
  - `first visit shows only the latest day and marks the window`: two days → `shown` has one
    day; history has the latest day's names with today and the older day's with `PAST`.
  - `same-day reload shows today's list again`: run `select` twice with the returned history →
    identical `shown`.
  - `next day shows only new repos`: a later `today` and a feed with one new repo on top.
  - `a repo that left the window and trends again stays hidden`: history has it with an old date.
  - `names compare case-insensitively`.
  - `load survives corrupt and throwing storage`: `getItem` returning `"{"` and throwing.
  - `select does not mutate inputs`.
- [ ] **Step 2: Run** `node --test tests/js/` → FAIL (module not found).
- [ ] **Step 3: Implement** `history.js`, then `app.js`, `index.html`, `style.css`.
- [ ] **Step 4: Run** `node --test tests/js/` → PASS.
- [ ] **Step 5: Visual check (controller).** The implementer writes a sample feed with two days,
  one failed source, two pending and a failed status into `<scratch>/site-sample/data/feed.json`,
  copies `site/` there, and reports the path; the controller serves it with
  `python3 -m http.server` and checks it in the browser at desktop and 400 px, including a
  reload.
- [ ] **Step 6: Commit** `feat: add the static page with browser history`

### Task 7: Workflow

**Files:**
- Create: `.github/workflows/daily.yml`

Contents:
- `on`: the four `schedule` crons from Global Constraints and `workflow_dispatch` with an input
  `mode` (`run` | `retry`, default `run`).
- `permissions: {contents: read, pages: write, id-token: write}`;
  `concurrency: {group: pages, cancel-in-progress: false}`.
- One job on `ubuntu-24.04`:
  1. `actions/checkout@v4`.
  2. Mode: `run` when `github.event.schedule == '0 6 * * *'` or the dispatch input is `run`;
     otherwise `retry`.
  3. `python3 -m trending_site <mode> --out site/data` with env `GEMINI_API_KEY`,
     `GEMINI_MODEL` from secrets and `GITHUB_TOKEN: ${{ github.token }}`;
     `continue-on-error: true`, `id: job`.
  4. If `hashFiles('site/data/feed.json') != ''`: `actions/upload-artifact@v4` (name
     `feed-${{ github.run_id }}`, path `site/data/feed.json`, `retention-days: 90`), then
     `actions/configure-pages@v5`, `actions/upload-pages-artifact@v3` (path `site`),
     `actions/deploy-pages@v4`.
  5. Last step: fail the workflow when `steps.job.outcome == 'failure'`, so GitHub emails the
     owner.
- Pages environment `github-pages` with the deploy URL output.

- [ ] **Step 1: Write the workflow.**
- [ ] **Step 2: Check** `uvx --from check-jsonschema check-jsonschema --builtin-schema
  vendor.github-workflows .github/workflows/daily.yml` → `ok`.
- [ ] **Step 3: Commit** `ci: add the daily Pages workflow`

### Task 8: README

**Files:**
- Rewrite: `README.md`

- [ ] **Step 1: Write the README** for users (English, no code blocks, remote links only):
  what the site is (public, per-browser history, 7-day window, same-day reload); how the repo is
  organized (tree); one-time setup — enable Pages with source "GitHub Actions", add the
  `GEMINI_API_KEY` secret (AI Studio, free tier), optional `GEMINI_MODEL`, first manual run from
  the Actions tab; schedule (03:00 São Paulo, retries 06:00/09:00/12:00, cron may run late or
  skip); failures (banner, email from GitHub on a failed run); the 60-day inactivity rule and how
  to re-enable the workflow; restoring a feed from a run's `feed-<id>` artifact by re-running
  with that file as `--feed` (or re-uploading it); privacy (everything public, history only in
  the visitor's browser).
- [ ] **Step 2: Commit** `docs: rewrite README for GitHub Pages`

### Task 9: Go live

Needs the user: push, Pages setting, secret.

- [ ] **Step 1:** Ask the user to set Pages source to "GitHub Actions" (Settings → Pages) and add
  the `GEMINI_API_KEY` secret; then merge and push.
- [ ] **Step 2:** `gh workflow run daily.yml -f mode=run`; `gh run watch` → success; the job log
  shows both sources and the summaries; no key in the log.
- [ ] **Step 3:** Open the Pages URL in the browser agent: today's list in Portuguese, reload
  shows the same list, the feed URL serves JSON.
- [ ] **Step 4:** Report repos published, failed sources, pending count, next scheduled run.
