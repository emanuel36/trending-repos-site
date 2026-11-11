# Trending repos on GitHub Pages — design

Supersedes `2026-11-11-trending-repos-site-design.md` (Oracle VM, Caddy, password, server-side
seen history). The code built from that design is replaced; parts of it are reused (see Code).

## Intent

The user follows new open-source projects through the `trending-repos` Claude Code skill: once a
day it collects the repositories trending on Trendshift and GitHub Trending that the user has not
seen yet, summarizes each README in Portuguese, and opens one page with all of them. Most trending
repos are not worth a visit, so the page lets the reader decide from a summary which ones to open
on GitHub. A repo is shown once and never comes back.

This project does the same job every day without the user's machine, for free, as a public
website. Other people may use it the way the user does: open it once a day in a browser. Each
visitor has their own history: a repo they have seen never comes back for them, independently of
other visitors.

## Goals

- **Same page as the skill and the previous build:** repos from Trendshift (daily ranking) and
  GitHub Trending (daily, all languages), each with its GitHub numbers, first non-badge README
  image, and a Portuguese summary with the fields `what`, `who`, `alternatives`, `maturity`,
  `translated_from`. Same look as the current template (`trending_site/assets/page.html` on
  `master`), sorted by stars within a day; the collapsed row shows the first sentence of `what`.
- **Per-visitor history in the browser:** a repo counts as seen as soon as it is displayed to
  that visitor; it never shows again in that browser on a later day. Reloading or reopening the
  page on the same day shows again what was first displayed that day (the user's decision), so a
  reload never yields an empty page. No account, login or button.
- **Catch-up window:** a returning visitor sees every repo they have not seen from the last 7
  days, grouped by day, newest day first. A first-time visitor (empty history) sees only the
  latest day. *(7 days and first-visit rule: designer's call, accepted by the user.)*
- **Runs unattended daily** on GitHub Actions at 03:00 America/Sao_Paulo (06:00 UTC; Brazil has
  no daylight saving time) — the user's decision.
- **Summaries by Gemini** (Google AI Studio API key, free tier).
- **Retry when Gemini fails,** at three levels (see Summaries).
- **Visible failures:** the page says which source failed for a day, how many repos are waiting
  for a summary, and when the last run failed and why.

## Non-goals

- Sharing history between a visitor's devices or browsers.
- Any interaction beyond reading: marking, starring, dismissing.
- Accounts, passwords, private access.
- History older than the 7-day window on the server.
- Sharing anything with the skill; the skill is left untouched.

## Anti-goals

- **A repo shown twice to the same browser,** or a repo that a visitor never got to see because a
  run failed (fetched, then dropped without being published).
- **A run that overwrites a good published feed with an empty or broken one.**
- **Secrets in git or in logs** (the Gemini key).
- **Data kept in git:** no commits of state or pages; the repo holds only code.
- **README text treated as instructions** by the model, or rendered unescaped in the page.
- **Changing the skill** under `~/.dotfiles/claude/skills/trending-repos`.

## Constraints

- **Cost:** free tiers only — GitHub (public repo, Actions, Pages), Gemini free tier.
- **Everything is public:** the page, the feed file and the pending queue (READMEs of public
  repos) are readable by anyone. That is acceptable.
- **GitHub Actions schedule:** cron runs in UTC, can start late (minutes to ~1 h) and is
  occasionally skipped; a scheduled workflow in a public repo is disabled after 60 days without
  repository activity. The README must say how to re-enable it.
- **Data-center IPs:** Actions runners may be blocked or rate-limited by Trendshift or GitHub
  Trending. A blocked source must not stop the other one, and the page shows which one failed.
- **Gemini free tier** limits requests per minute and per day.
- **Browser storage:** history lives in `localStorage` of `emanuel36.github.io`, an origin shared
  with any other Pages site of that account; keys must be namespaced for this project.
- **Code conventions:** Python 3.12 standard library only at runtime, 100-char lines, absolute
  imports, Google-style docstrings on non-trivial public APIs, ruff, pytest. Personal public repo
  `github.com/emanuel36/trending-repos-site`.

## Decided with the user

- Public site on GitHub Pages, no password.
- No versioning of data: nothing the job produces is committed.
- Per-visitor history, in the browser; seen = displayed; a same-day reload shows today's list
  again.
- Main run at 03:00 São Paulo.
- Visitors use one browser; cross-device history does not matter.
- Retry system for Gemini failures, including extra attempts on the same day.
- The architecture below is the designer's proposal; the user asked for a clean design even if it
  means rewriting the code.

## How it works (designer's calls)

### Data: one public feed

The site serves `data/feed.json`, the only state of the system:

- `days`: the days of the last 7 calendar days (São Paulo dates, today included) that have
  content, newest first *(calendar days: designer's call)*. Each day: `date` (São Paulo date),
  `repos` (published repos with their GitHub fields and `summary` or null), `failed` (sources that
  failed in every run of that day).
- `pending`: repos fetched but not yet published, oldest first, each with its fetched data
  (including the README capped at 8000 chars), the day it was first fetched, its own failure
  count and the date of its last own failure.
- `status`: outcome of the last run (`ok`, time, reason).

A repo appears at most once across `days` and `pending`. A repo that trends again after leaving
the window is treated as new; browsers that saw it keep hiding it.

### The daily job

One entry point, e.g. `python3 -m trending_site run`, does:

1. Download the published `data/feed.json` from the Pages URL. 404 on the very first run means
   an empty feed. Any other failure, or a file that does not parse or validate, stops the run
   without publishing.
2. Fetch both sources; skip repos already in the feed; enrich the new ones (GitHub API, token
   provided by Actions) and append them to `pending`.
3. Summarize `pending` oldest first, up to 30 repos published per day.
4. Add the published repos to today's day, drop days older than 7 days, record `status`.
5. Write the new `data/feed.json` next to the static page; the workflow publishes it.

`python3 -m trending_site retry` does steps 1, 3, 4 and 5 only: it works through `pending`
without fetching the sources, and publishes nothing when `pending` is empty. When the feed shows
that no fetch has happened yet today (the 03:00 run was skipped or failed before fetching),
`retry` does the full `run` instead, so a skipped cron does not lose a day *(designer's call;
the feed records the date of the last successful fetch)*.

### Summaries and retry

- One Gemini `generateContent` call per repo with a JSON response schema matching the summary
  fields and the `maturity` enum; the prompt carries the skill's step-2 rules (Portuguese; first
  sentence of `what` stands alone; "Parecido com X, mas Y"; README language in Portuguese when it
  is neither pt nor en; empty README → summarize from the description and say so); README text
  is fenced as material, never instructions; output validated before it is published.
- **Per call:** backoff on 429, 5xx, broken connections and invalid output; honor the API's
  retry delay, clamped. Calls are spaced to respect the per-minute limit.
- **Same day:** the workflow also runs `retry` at 06:00, 09:00 and 12:00 São Paulo, so most
  failures are resolved before the morning
  *(hours: designer's call)*.
- **Across days:** what is still pending waits for the next run. Daily quota or Gemini being down
  never counts against a repo. A repo's own failure (refusal, invalid output after retries,
  `INVALID_ARGUMENT`) counts once per day; after 3 days it is published with only its GitHub
  description.
- A run in which Gemini was unavailable for every attempt, or refused every repo, is recorded as
  a failed run with its reason; a rejected key or model is a failed run.

### Pages and browser history

- Static `index.html`, script and styles live in the repo, with the current template's look. The
  page loads `data/feed.json` and renders client-side; all third-party text is escaped and URLs
  restricted to http(s).
- History: the repo names this browser has displayed, each with the local date it was first
  displayed, in `localStorage` under a project-specific key, kept indefinitely (a few KB per
  year). On load the page shows the repos of the window that are unseen or were first displayed
  today (or only the latest day for an empty history), then marks the new ones seen. On a
  first visit every repo in the window is marked seen, not only the latest day, so the second
  visit does not bring back up to six older days at once; the repos not displayed are recorded
  with a past date, so a same-day reload still shows only the latest day *(designer's call)*.
- The header shows the count per source, failed sources, the number pending, and "última execução
  falhou em DD/MM: <motivo>" when the last run failed.
- If `localStorage` is unavailable (private window, blocked), the page still renders the latest
  day without remembering.

### Workflow

- `.github/workflows/` with the schedules (03:00 run; 06:00, 09:00, 12:00 retry; São Paulo
  times written in UTC) and manual dispatch; one run at a time (concurrency group).
- Steps: checkout code, run the job, upload the static page plus `data/` as the Pages artifact,
  deploy. A failed run still publishes its updated `status` when the downloaded feed was valid.
- Each run also uploads `feed.json` as a workflow artifact (90-day retention) so a bad deploy can
  be undone by hand; the README says how.
- Secrets: `GEMINI_API_KEY` (and optional `GEMINI_MODEL`) as repository secrets; GitHub API calls
  use the workflow's own token.

### Code

- Reuse from `master`: source scraping and enrichment (`sources.py`), the Gemini client
  (`gemini.py`), summary schema, prompt and checks (`summary.py`), and their tests.
- New: pure feed logic (merge a day, dedupe, prune the window, pending queue and failure counts),
  the download/publish of the feed, the static page, the workflow, a README for users.
- Removed: file-based state and server-side seen history, HTML rendering in Python and the page
  repair step, `deploy/` (systemd, Caddy, setup script), the password.

## Left to the builder

- Module layout, function names, test structure, whether the page script gets tests.
- Exact feed field names beyond those above.
- Retry numbers and Gemini call spacing.
- Default Gemini model: the cheapest current Flash model that supports structured output, checked
  against Google's docs (`gemini-3.5-flash-lite` on 2026-11-11).
- Whether `retry` is a separate command or a flag.
