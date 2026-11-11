"""Walks the pending queue through Gemini, publishing what gets summarized."""

import logging
from dataclasses import dataclass

from trending_site import gemini

log = logging.getLogger(__name__)

MAX_FAILURES = 3
MAX_PER_DAY = 30
REASON_CHARS = 120


@dataclass
class Outcome:
    """What one walk of the queue produced."""

    published: list[dict]
    kept: list[dict]
    unavailable: str | None
    all_refused: str | None


def work(entries: list[dict], client, day: str, limit: int = MAX_PER_DAY) -> Outcome:
    """Summarizes queued repos oldest first.

    Stops at `limit` published repos (the rest stay queued) and when Gemini is unavailable
    (the rest stay queued, counts untouched). A refused summary counts once per `day`; at
    `MAX_FAILURES` the repo is published without a summary. When at least two repos were tried
    and all were refused, `all_refused` is set; the repos at `MAX_FAILURES` are still published
    without a summary and the others are kept with their new counts.
    Entry dicts may get new `failures`/`last_failure`; `entries` itself is not modified.

    Args:
        entries: Pending entries with `repo`, `queued`, `failures` and `last_failure`.
        client: Object with `summarize(repo) -> dict`.
        day: Today's date, `YYYY-MM-DD`.
        limit: Maximum repos to publish.

    Returns:
        The published repos, the entries to keep, the unavailability reason (or None) and the
        last refusal reason when every try was refused (or None).

    Raises:
        Exception: anything other than `Unavailable` and `SummaryFailed` from the client.
    """
    queue = sorted(entries, key=lambda item: item["queued"])
    published, kept = [], []
    unavailable = refused = None
    tried = refusals = 0
    for index, item in enumerate(queue):
        if len(published) >= limit:
            kept.extend(queue[index:])
            break
        name = item["repo"]["name"]
        tried += 1
        try:
            result = client.summarize(item["repo"])
        except gemini.Unavailable as error:
            log.warning("Gemini unavailable, leaving %d repos queued: %s",
                        len(queue) - index, error)
            unavailable = str(error)[:REASON_CHARS]
            kept.extend(queue[index:])
            break
        except gemini.SummaryFailed as error:
            refusals += 1
            refused = str(error)[:REASON_CHARS]
            if item["last_failure"] != day:
                item["failures"] += 1
                item["last_failure"] = day
            if item["failures"] >= MAX_FAILURES:
                log.warning("%s: summary failed on %d days, publishing without it: %s",
                            name, item["failures"], error)
                published.append({**item["repo"], "summary": None})
            else:
                log.warning("%s: summary failed (%d): %s", name, item["failures"], error)
                kept.append(item)
            continue
        log.info("%s: summarized", name)
        published.append({**item["repo"], "summary": result})

    # Gemini refusing every repo points at our schema or config, not at the repos.
    all_refused = refused if refusals >= 2 and refusals == tried else None
    return Outcome(published, kept, unavailable, all_refused)
