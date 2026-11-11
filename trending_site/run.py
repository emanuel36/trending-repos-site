"""The daily run: fetch, queue, summarize, publish, and record the outcome.

A repo is marked seen only after the day page that lists it is written, and a failed run
publishes nothing: it records its status and re-renders only the index.
"""

import dataclasses
import datetime
import logging
import os
import pathlib
import re
import zoneinfo

from trending_site import gemini, render, sources
from trending_site.state import State, StateError

TZ = zoneinfo.ZoneInfo("America/Sao_Paulo")
MAX_FAILURES = 3
REASON_CHARS = 200
UNAVAILABLE_CHARS = 120
STATE_FILE = re.compile(r"(?:cannot read|invalid JSON in) (.+?\.json)")

log = logging.getLogger(__name__)


class RunFailed(Exception):
    """The run cannot go on; the message is the Portuguese reason shown on the index."""


@dataclasses.dataclass
class Config:
    """Where the run keeps its state and writes the site, and how much it publishes."""

    state_dir: pathlib.Path
    site_dir: pathlib.Path
    max_repos: int = 30


def config_from_env() -> tuple[Config, str | None]:
    """Builds the configuration from TRENDING_STATE_DIR, TRENDING_SITE_DIR and
    TRENDING_MAX_REPOS.

    Returns:
        The configuration and None, or, when TRENDING_MAX_REPOS is not a positive integer, a
        configuration with the default cap and the Portuguese reason for the failed run.
    """
    config = Config(
        state_dir=pathlib.Path(os.environ.get("TRENDING_STATE_DIR", "/var/lib/trending-site")),
        site_dir=pathlib.Path(os.environ.get("TRENDING_SITE_DIR", "/srv/trending-site")),
    )
    value = os.environ.get("TRENDING_MAX_REPOS") or str(config.max_repos)
    try:
        max_repos = int(value)
    except ValueError:
        max_repos = 0
    if max_repos < 1:
        return config, f"TRENDING_MAX_REPOS inválido: {value}"
    return dataclasses.replace(config, max_repos=max_repos), None


def today(now: datetime.datetime) -> str:
    """Returns the São Paulo date (YYYY-MM-DD) of an aware datetime."""
    return now.astimezone(TZ).date().isoformat()


def merge(published: list[dict], day: str, previous: dict | None) -> list[dict]:
    """Returns the day's repos: those already on the page plus the new ones, by stars.

    Args:
        published: queue repos with a "summary" key; their README is dropped.
        day: the page date.
        previous: the page data already saved for `day`, or None.
    """
    page = {}
    if previous and previous.get("date") == day:
        page = {repo["name"]: repo for repo in previous["repos"]}
    for repo in published:
        page[repo["name"]] = {key: value for key, value in repo.items() if key != "readme"}
    return sorted(page.values(), key=lambda repo: (repo["stars"] is None, -(repo["stars"] or 0)))


def _reason(error: Exception, state_dir: pathlib.Path) -> str:
    if isinstance(error, RunFailed | gemini.AuthError):
        return str(error)
    if isinstance(error, sources.GitHubAuthError):
        return "token do GitHub rejeitado"
    if isinstance(error, StateError):
        match = STATE_FILE.search(str(error))
        path = pathlib.Path(match[1]) if match else None
        if path and path.is_relative_to(state_dir):
            path = path.relative_to(state_dir)
        return f"estado corrompido: {path or error}"
    return repr(error)[:REASON_CHARS]


def _render_index(state: State, site_dir: pathlib.Path, status: dict) -> None:
    try:
        days = state.days()
        latest = state.day(days[0]) if days else None
    except StateError as error:
        log.error("cannot read the day pages for the index: %s", error)
        days, latest = [], None
    render.write(site_dir, "index.html", render.index_html(latest, days, status))


def fail(config: Config, now: datetime.datetime, reason: str) -> int:
    """Records a failed run: saves its status when possible and re-renders only the index.

    Args:
        config: the run configuration.
        now: when the run started, timezone-aware.
        reason: why it failed, in Portuguese, shown on the index.

    Returns:
        1, the exit status of a failed run.
    """
    log.error("run failed: %s", reason)
    state = State(config.state_dir)
    status = {"ok": False, "at": now.isoformat(), "reason": reason}
    try:
        state.save_status(False, status["at"], reason)
    except OSError as error:
        log.error("cannot save the status: %s", error)
    _render_index(state, config.site_dir, status)
    return 1


def run(config: Config, client, now: datetime.datetime, fetch=sources.fetch,
        enrich=sources.enrich) -> int:
    """Runs the daily job once.

    Args:
        config: where the state and the site live, and the per-run cap.
        client: has `summarize(repo) -> dict`, raising the `gemini` exceptions.
        now: when the run started, timezone-aware; its São Paulo date is the page date.
        fetch: `sources.fetch` or a replacement.
        enrich: `sources.enrich` or a replacement.

    Returns:
        0 when the run succeeded, 1 when it failed.
    """
    try:
        _publish(config, client, now, fetch, enrich)
        state = State(config.state_dir)
        status = {"ok": True, "at": now.isoformat(), "reason": None}
        state.save_status(**status)
        _render_index(state, config.site_dir, status)
    except Exception as error:  # noqa: BLE001 - any failure must be recorded on the index
        log.exception("run failed")
        return fail(config, now, _reason(error, config.state_dir))
    log.info("run done")
    return 0


def _repair_day_pages(state: State, site_dir: pathlib.Path) -> None:
    """Re-renders each day page whose HTML is missing or older than its saved data or the template.

    A run that saved the day data but died before writing its HTML leaves such a page; it must
    be written before `reconcile` marks its repos seen. A newer template (after `git pull`)
    reaches the old days too.
    """
    template = render.TEMPLATE.stat().st_mtime
    for date in state.days():
        data = state.root / "days" / f"{date}.json"
        html = site_dir / f"{date}.html"
        if html.exists() and html.stat().st_mtime >= max(data.stat().st_mtime, template):
            continue
        log.info("re-rendering the day page of %s", date)
        render.write(site_dir, f"{date}.html", render.day_html(state.day(date)))


def _publish(config: Config, client, now: datetime.datetime, fetch, enrich) -> None:
    day = today(now)
    state = State(config.state_dir)
    _repair_day_pages(state, config.site_dir)
    log.info("run for %s: reconciling the state", day)
    state.reconcile()

    queue = state.queue()
    known = {name.lower() for name in state.seen()}
    known |= {item["repo"]["name"].lower() for item in queue}
    repos, failed = fetch(known)
    for source in failed:
        log.warning("source %s failed: %s", source["source"], source["error"])
    if len(failed) >= 2:
        raise RunFailed("Trendshift e GitHub Trending falharam")
    log.info("fetched %d new repos", len(repos))

    for repo in repos:
        try:
            enriched = enrich(repo)
        except sources.EnrichFailed as error:
            # Not queued and not seen, so it comes back on a later run if still trending.
            log.warning("skipping %s for today: %s", repo["name"], error)
            continue
        queue.append({"repo": enriched, "queued": day, "failures": 0, "last_failure": None})
    state.save_queue(queue)
    log.info("queue holds %d repos", len(queue))

    published, kept = [], []
    unavailable = None
    refusals, refused = 0, None
    for index, item in enumerate(queue):
        if len(published) >= config.max_repos:
            kept.extend(queue[index:])
            break
        name = item["repo"]["name"]
        try:
            result = client.summarize(item["repo"])
        except gemini.Unavailable as error:
            log.warning("Gemini unavailable, leaving %d repos queued: %s",
                        len(queue) - index, error)
            unavailable = error
            kept.extend(queue[index:])
            break
        except gemini.SummaryFailed as error:
            refusals, refused = refusals + 1, error
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

    if refusals >= 2 and refusals == len(queue):
        # Gemini refusing every repo points at our schema or config, not at the repos: publish
        # nothing, but keep the counts, which are still once per repo and day.
        state.save_queue(queue)
        raise RunFailed(f"Gemini recusou todos os resumos: {str(refused)[:UNAVAILABLE_CHARS]}")

    if published:
        previous = state.day(day)
        if previous:
            # A source that worked in an earlier run of the day did bring repos to this page.
            before = {source["source"] for source in previous.get("failed", [])}
            failed = [source for source in failed if source["source"] in before]
        page = {"date": day, "repos": merge(published, day, previous), "failed": failed,
                "queued": len(kept)}
        state.save_day(page)
        render.write(config.site_dir, f"{day}.html", render.day_html(page))
        log.info("published %d repos on %s", len(published), day)
        state.add_seen([repo["name"] for repo in published], day)
    else:
        log.info("nothing published")
    state.save_queue(kept)
    if unavailable and not published:
        # Quota hit mid-run is normal; Gemini down before any summary must show on the index.
        raise RunFailed(f"Gemini indisponível: {str(unavailable)[:UNAVAILABLE_CHARS]}; "
                        f"{len(kept)} na fila")
