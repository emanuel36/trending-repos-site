# Trending repos site — design

## Intent

The user follows new open-source projects through the `trending-repos` Claude Code skill
(`~/.dotfiles/claude/skills/trending-repos`): once a day it collects the repositories trending on
Trendshift and GitHub Trending that the user has not seen yet, summarizes each README in
Portuguese, and opens one page with all of them. Most trending repos are not worth a visit
(READMEs only in Chinese, a few lines with no screenshots), so the page lets the user decide from
a summary which ones to open on GitHub. A repo is listed once and never comes back.

The skill only runs when the user's machine is on and a Claude Code session starts. This project
does the same job on a free server, every day, without the user's machine, and serves the result
as a password-protected website.

## Goals

- **Same page as the skill:** the unseen repos of the day from Trendshift (daily ranking) and
  GitHub Trending (daily, all languages), each with its GitHub numbers, first README image, and a
  Portuguese summary with the same fields as the skill: `what`, `who`, `alternatives`,
  `maturity`, `translated_from`. Sorted by stars; the collapsed row shows the first sentence of
  `what`.
- **Runs unattended once a day** on an Oracle Cloud Always Free VM.
- **Summaries by Gemini** through a Google AI Studio API key (free tier), in place of Claude.
- **Served by Caddy** over HTTPS with automatic certificates and HTTP basic authentication.
- **Seen history kept on the server:** a repo is recorded as seen when it is published on a page,
  and never shows up on a later page.
- **Past days stay reachable:** one page per day and an index linking to all of them *(builder's
  call, see below)*.

## Non-goals

- Sharing the seen history with the skill. The two are independent; a repo can appear in both.
- Marking repos as seen, starring, or any other interaction from the browser. The site is static.
- Multiple users, accounts, or per-user history.
- Notifications (email, Slack, push) when the page is ready.

## Anti-goals

- **Changing the skill.** Nothing under `~/.dotfiles/claude/skills/trending-repos` is modified;
  code is copied into this repo, not imported from there.
- **A page reachable without the password**, or over plain HTTP other than Caddy's redirect.
- **Secrets in git:** the Gemini key, the GitHub token and the password hash live only on the VM.
- **A repo appearing on two days' pages**, or a repo silently lost (fetched, not published, and
  marked as seen).
- **Keep-alive tricks** (burning CPU) to dodge Oracle's idle-VM reclamation.

## Constraints

- **Cost:** everything stays inside free tiers — Oracle Always Free, Gemini API free tier, a free
  DuckDNS subdomain, Let's Encrypt.
- **Gemini free tier limits** requests per minute and per day, and Google may use free-tier input
  for training; READMEs are public, so that is acceptable.
- **Data-center IP:** Trendshift and GitHub Trending are scraped from HTML and may block or
  rate-limit Oracle's IPs. A blocked source must not stop the other one (as in the skill), and
  the page must say which source failed.
- **README text is third-party input:** the prompt to Gemini treats it as material to summarize,
  never as instructions, and the output is validated against a schema before rendering.
- **Code conventions:** Python 3, 100-char lines, absolute imports, Google-style docstrings on
  non-trivial public APIs, ruff, pytest. Personal repo under `github.com/emanuel36`.

## Decided with the user

- Hosting on Oracle Cloud Always Free, served by Caddy.
- Access protected by authentication (password), over the public internet through a domain — not
  restricted to Tailscale.
- Gemini through Google AI Studio for the summaries.
- A separate project in `~/Documents/trending-repos-site`, its own git repository; the skill is
  left untouched.
- The page is the same idea as the skill's page.
- The GitHub repository (`github.com/emanuel36/trending-repos-site`) is public.
- **Scope includes the server:** the user creates the Oracle account and VM (it needs a credit
  card) and hands over SSH access; the builder then installs everything on the VM, runs the job
  once, and checks end to end that the site opens with the password. This also answers early
  whether Trendshift and GitHub Trending block Oracle's IPs.

## How it works (builder's calls, marked as such)

Everything below is the designer's proposal; the builder may change details that do not affect
the goals.

### Code

- Start from a copy of the skill's `scripts/trending.py`, `scripts/render_page.py`,
  `assets/page.html` and their tests. Keep the sources, the enrichment (GitHub numbers, first
  non-badge image, README capped at 8000 chars), the summary fields and their validation.
- Drop what only made sense locally: opening Chrome, the `gh auth token` fallback, `catch_up.sh`,
  `/tmp` work directories.
- Standard library only, including the Gemini call (REST through `urllib`), so the VM needs no
  virtualenv. The builder may add a dependency if a real need shows up.
- One entry point, e.g. `python3 -m trending_site run`, does the whole day: fetch → summarize →
  render → mark seen.

### Summaries

- One Gemini `generateContent` call per repo, with a JSON response schema matching the summary
  fields and the `maturity` enum. The prompt carries the same instructions as the skill's step 2
  (Portuguese; first sentence of `what` stands alone; "Parecido com X, mas Y"; README language in
  Portuguese when it is neither pt nor en; empty README → summarize from the description and say
  so).
- Model set by `GEMINI_MODEL`; default to the cheapest current Flash model that supports
  structured output, checked against Google's docs at implementation time.
- Calls are spaced to respect the per-minute limit and retried with backoff on 429/5xx.
- **Pending queue** *(designer's call)*: a repo that is fetched but not published — its summary
  failed, the daily quota ran out, or it was past the 30-repo cap — is stored in the state
  directory with its fetched data (numbers, image, README). Each run first works through the
  queue, oldest first, then the newly fetched repos, so a repo is never lost because it left
  the trending lists. A queued repo that shows up in the sources again is not duplicated.
- **Quota exhaustion** *(designer's call)*: when Gemini reports the daily quota used up, the run
  stops calling it, publishes what it has, and leaves the rest queued for the next day. This
  does not count as a failure of those repos. Everything stays on the free tier.
- **A repo whose summary fails** for a reason of its own (refusal, invalid output after
  retries) stays queued. After it has failed on 3 separate days it is published with only its
  GitHub description (as the skill does for a repo without a summary) and marked seen, so a
  README Gemini always refuses does not loop forever.

### Pages

- `/<YYYY-MM-DD>.html` per day, rendered from the copied template; a second run on the same day
  adds its new repos to that day's page (same as the skill).
- `/index.html` shows the latest day's page content or redirects to it, and links to every
  previous day, newest first.
- Each page shows, like the skill's final report: how many repos, from which sources, any source
  that failed, and how many are waiting in the queue.
- **Run status on the index** *(designer's call)*: every run records its outcome; when the last
  run failed (revoked key, expired token, both sources blocked), the index shows "última
  execução falhou em DD/MM: <motivo>" so a broken job does not go unnoticed for weeks. This is
  the only thing a failed run re-renders; day pages stay untouched.
- Days are kept indefinitely; they are a few hundred KB each.

### Schedule and state

- A systemd timer runs the job daily at 07:00 America/Sao_Paulo, with `Persistent=true` so a run
  missed while the VM was down happens at boot.
- The job runs as a dedicated unprivileged user.
- State (`seen.json`, the pending queue with failure counts, per-day page data, last run
  status) in `/var/lib/trending-site/`;
  rendered HTML in `/srv/trending-site/`, read by Caddy.
- Seen repos are recorded only after the day's HTML is written successfully.
- Logs go to the journal; a failed run leaves the previous pages untouched.

### Serving

- Caddy serves `/srv/trending-site/` with `file_server` and `basic_auth` for every path, on a
  DuckDNS subdomain, with automatic HTTPS.
- The VM gets a reserved (static) public IP, so DuckDNS is set once; no IP updater.
- Ports 80 and 443 opened in both the VCN security list and the VM's own iptables rules (the
  Oracle Ubuntu image blocks them by default).

### Secrets and configuration

- `/etc/trending-site/env`, readable only by root and loaded by the systemd unit
  (`EnvironmentFile`): `GEMINI_API_KEY`, `GITHUB_TOKEN` (a fine-grained token with no scopes,
  needed because unauthenticated GitHub API calls are limited to 60/hour and a day needs about
  60), optionally `GEMINI_MODEL`.
- The basic-auth bcrypt hash (from `caddy hash-password`) is injected into the Caddyfile through
  an environment placeholder, so the Caddyfile in git holds no secret.

### Deployment

- The repo holds a `deploy/` directory: the systemd service and timer, the Caddyfile, and an
  idempotent `setup.sh` run on the VM (installs Caddy and Python, creates the user and
  directories, installs the units, opens the firewall).
- The VM clones the public repo over HTTPS (no key). Updating is: the user pushes, then
  `git pull` and `setup.sh` on the VM. The builder never pushes; the user pushes the first
  version before the VM is set up.
- The builder runs `setup.sh` over the SSH access the user provides, asks the user to put the
  secrets in `/etc/trending-site/env` and choose the password, runs the job once, and checks
  the site over HTTPS with and without the password.
- A README for the user: what the site is, how the repo is organized, and the one-time steps
  that cannot be scripted — creating the Oracle account and VM, reserving the IP, the DuckDNS
  subdomain, the AI Studio key, the GitHub token, choosing the password.
- The README notes that Oracle may reclaim an idle Always Free VM and that upgrading the account
  to Pay As You Go (still free within Always Free limits) avoids it.

## Left to the builder

- Module and file layout, function names, test structure.
- The exact prompt wording, retry and spacing numbers.
- Whether the index redirects to the latest day or embeds it.
- Small visual additions to the copied template (date navigation), keeping its look.
