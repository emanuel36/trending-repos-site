# Trending repos site

A public website with the GitHub repositories that are trending today and that you have not seen
yet. Every morning a job reads the Trendshift daily ranking and the GitHub Trending daily
ranking, summarizes each README in Portuguese with Gemini, and publishes the result on GitHub
Pages. A repo is shown to you once and does not come back.

Site: https://emanuel36.github.io/trending-repos-site/

Repository: https://github.com/emanuel36/trending-repos-site

The idea comes from a Claude Code skill that does the same job on one machine. This project does
it every day on free GitHub services, so it does not depend on that machine being on.

## What the page shows

- **Repos by day:** each repo has its GitHub numbers, the first README image and a summary in
  Portuguese. A collapsed row shows the first sentence of what the repo is; repos are sorted by
  stars within a day.
- **Summary fields:** what it is, who it is for, alternatives, maturity and, when the README is
  not in Portuguese or English, the language it was translated from.
- **Header:** how many repos are shown, how many came from each source, which source failed on
  the day of the latest update and how many repos are still waiting for a summary.
- **Failure banner:** when the last run failed, the page says on which day and why.
- **Stale banner:** when the site has not updated for more than 30 hours, the page says
  since which day.

## How your history works

- **Per browser:** the page remembers in your browser which repos it has shown you. There is no
  account and no login, and nobody else sees your history.
- **Seen once:** a repo you have seen does not show again on a later day, even if it keeps
  trending.
- **Same-day reload:** reloading the page on the same day shows again what it first showed you
  that day, so a reload never gives an empty page.
- **Catching up:** if you skip some days, the page shows what you have not seen from the last
  7 days, grouped by day, newest first.
- **First visit:** you see only the latest day. Older repos in the window are marked as seen, so
  your second visit does not bring back a week of repos at once.
- **Limits:** history is not shared between browsers or devices. Clearing the site data in your
  browser starts it over. If the browser blocks site data, the page shows the latest day and
  remembers nothing.

## How it works

- **Schedule:** the main run starts at 03:00 São Paulo time. Retries start at 06:00, 09:00 and
  12:00 and summarize whatever is still waiting; if the main run did not fetch the rankings,
  the first retry does it. GitHub may start a scheduled run late or skip it.
- **Limits:** at most 30 repos are published per day. The rest wait for the next run.
- **Spam:** a repo with 200 or more stars and no forks is dropped before it is summarized.
  Bought stars come without forks; real repos that popular almost always have some. A real
  repo dropped on its first day comes back on a later day once someone forks it.
- **Model:** summaries use the Gemini model gemini-3.5-flash-lite by default. The optional
  secret GEMINI_MODEL selects another one.
- **Failed summaries:** when Gemini is down or out of quota, repos wait and nothing counts
  against them. A repo whose own summary fails is retried on up to three separate days. After
  that it is published with only its GitHub description.
- **Failed sources:** if Trendshift or GitHub Trending cannot be read, the other one is still
  used and the page says which one failed.
- **Window:** the site keeps the last 7 days. Nothing the job produces is committed to git.

## How the repo is organized

- **trending_site/:** the job that fetches sources, summarizes and updates the feed
- **site/:** the static page: HTML, styles and the script with the browser history
- **.github/:** the workflow that runs the job and deploys the site
- **tests/:** automated tests for the job and the page script
- **docs/:** design and implementation plan

## One-time setup

1. **Enable Pages:** in the repository settings, under Pages, set the source to GitHub Actions.
2. **Add the Gemini key:** create a free API key in Google AI Studio
   (https://aistudio.google.com) and save it as the repository secret GEMINI_API_KEY, under
   Settings, Secrets and variables, Actions.
3. **Choose a model (optional):** save the repository secret GEMINI_MODEL to use a model other
   than the default.
4. **Default branch:** the workflow must be on the default branch (master) before Run workflow
   appears in the Actions tab.
5. **First run:** open the Actions tab, choose the daily workflow, select Run workflow, pick
   the mode run and check first run. The site is live when the run ends. The GitHub token
   needs no setup: the workflow provides its own.
6. **Later runs:** never check first run again. Without it, a run that finds no published feed
   fails and publishes nothing, so a lost feed never wipes the history.

The workflow has two modes in a manual run. Run fetches the rankings and summarizes. Retry only
summarizes what is waiting.

## When something fails

- **On the page:** the banner shows the failure of the last run, and the header shows a failed
  source and the number of repos waiting.
- **By email:** GitHub emails you when a workflow run fails. A failed run still publishes its
  status, so the banner appears.
- **Unreadable feed:** if the published feed cannot be read or is missing, the run stops and
  publishes nothing, so the site keeps its last version. The failed-run email from GitHub
  tells you, and after 30 hours the page shows the stale banner.
- **Missing key:** if GEMINI_API_KEY is not set, the run fails with "chave do Gemini ausente".
- **Rejected key or token:** the banner says so, and the next run fixes it once the secret is
  corrected.

## Scheduled runs stop after 60 days

GitHub disables scheduled workflows in a public repository after 60 days without repository
activity. To turn them back on, open the Actions tab, choose the daily workflow and select
Enable workflow. A manual run also works while you wait for the next schedule.

## Restoring an earlier feed

Every run keeps its feed for 90 days. To publish an earlier one again:

1. **Find a good run:** open the Actions tab and pick the run whose feed was good.
2. **Note its run id:** it is the number at the end of the run's address.
3. **Run the workflow manually:** choose the daily workflow, select Run workflow and type that
   number in the restore run id field. The site then shows that run's feed.

## Privacy

- **Everything is public:** the page, the feed file and the queue of repos waiting for a summary
  are readable by anyone. They only contain data about public repositories.
- **History stays with you:** the list of repos you have seen exists only in your browser and
  is never sent anywhere.
- **Secrets:** the Gemini key lives only in the repository secrets and is not written to logs.
