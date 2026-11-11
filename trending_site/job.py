"""One step of the daily job: fetch, queue, summarize, publish and prune the feed."""

import logging
import zoneinfo
from datetime import datetime

from trending_site import feed as feed_model
from trending_site import gemini, pending, sources

FEED_URL = "https://emanuel36.github.io/trending-repos-site/data/feed.json"
TZ = zoneinfo.ZoneInfo("America/Sao_Paulo")
SOURCE_COUNT = 2

log = logging.getLogger(__name__)


def now() -> datetime:
    """Returns the current time in São Paulo."""
    return datetime.now(TZ)


def today(moment: datetime) -> str:
    """Returns the São Paulo date (YYYY-MM-DD) of an aware datetime."""
    return moment.astimezone(TZ).date().isoformat()


def new_client(api_key: str, model: str) -> gemini.Client:
    """Builds the Gemini client the job summarizes with."""
    return gemini.Client(api_key, model)


def _fetch(feed: dict, day: str, fetch, enrich) -> list[dict]:
    """Queues today's new repos; returns the sources that failed."""
    repos, failed = fetch(feed_model.known_names(feed))
    for item in failed:
        log.warning("source %s failed: %s", item["source"], item["error"])
    enriched = []
    for repo in repos:
        try:
            enriched.append(enrich(repo))
        except sources.EnrichFailed as error:
            log.warning("skipping %s: %s", repo["name"], error)
    feed_model.add_pending(feed, enriched, day)
    if len(failed) < SOURCE_COUNT:
        feed["last_fetch"] = day
    log.info("fetched %d new repos, %d queued", len(enriched), len(feed["pending"]))
    return failed


def step(feed: dict, client, now: datetime, *, fetch_sources: bool, fetch=sources.fetch,
         enrich=sources.enrich) -> str | None:
    """Runs one step of the job on `feed`, in place.

    Fetches the sources when asked, then works the pending queue through Gemini, publishes
    what came out on today's day, up to `pending.MAX_PER_DAY` repos a day across steps, and
    drops the days outside the window. `last_fetch` moves to today only when at least one
    source could be read. A day records the sources that failed in every fetch of that day: the
    first fetch records its failed sources, even when nothing is published, and each later
    fetch keeps only those that failed again, so a source that fails only in a later fetch is
    not added. A step that does not fetch leaves the day's failed sources as they are.

    Args:
        feed: A valid feed; modified in place.
        client: Object with `summarize(repo) -> dict`, raising the `gemini` exceptions.
        now: When the job started, timezone-aware; its São Paulo date is the feed day.
        fetch_sources: Whether to read the rankings before working the queue.
        fetch: `sources.fetch` or a replacement.
        enrich: `sources.enrich` or a replacement.

    Returns:
        The Portuguese reason of a failed step, or None when it succeeded.

    Raises:
        sources.GitHubAuthError: The GitHub token was rejected.
        gemini.AuthError: Gemini rejected the key or the request.
    """
    day = today(now)
    reason = None
    failed = None
    if fetch_sources:
        failed = _fetch(feed, day, fetch, enrich)
        if len(failed) >= SOURCE_COUNT:
            reason = "Trendshift e GitHub Trending falharam"
    # The daily cap spans every step of the day, not just this one.
    published_today = sum(len(d["repos"]) for d in feed["days"] if d["date"] == day)
    limit = max(0, pending.MAX_PER_DAY - published_today)
    outcome = pending.work(feed["pending"], client, day, limit)
    feed["pending"] = outcome.kept
    # A fetch records its failed sources on today's day, or clears those that recovered, even
    # when nothing could be published.
    day_exists = any(d["date"] == day for d in feed["days"])
    if outcome.published or failed or (failed is not None and day_exists):
        feed_model.publish(feed, outcome.published, day, failed)
        if outcome.published:
            log.info("published %d repos on %s", len(outcome.published), day)
        else:
            log.info("updated failed sources on %s", day)
    if reason is None and outcome.all_refused:
        reason = f"Gemini recusou todos os resumos: {outcome.all_refused}"
    if reason is None and outcome.unavailable and not outcome.published:
        reason = f"Gemini indisponível: {outcome.unavailable}; {len(feed['pending'])} na fila"
    feed_model.prune(feed, day)
    return reason
