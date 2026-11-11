"""Command line: `python3 -m trending_site run|retry` updates the feed once.

`run` fetches the rankings and works the pending queue; `retry` only works the queue, unless
no fetch happened today, in which case it does a full `run`. Both write `feed.json` to `--out`,
a failed run included, so its status is published; an unreadable or missing feed, or a new
feed that fails its checks, writes nothing. `--first-run` lets a missing feed start empty.
"""

import argparse
import logging
import os
import pathlib
import sys

from trending_site import feed as feed_model
from trending_site import gemini, job, sources, store

REASON_CHARS = 200

log = logging.getLogger("trending_site")


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="trending_site")
    parser.add_argument("command", choices=["run", "retry"],
                        help="run: fetch and summarize; retry: summarize what is pending")
    parser.add_argument("--feed", default=job.FEED_URL, metavar="SOURCE",
                        help="URL or path of the published feed (default: %(default)s)")
    parser.add_argument("--out", default="site/data", type=pathlib.Path, metavar="DIR",
                        help="directory to write feed.json to (default: %(default)s)")
    parser.add_argument("--first-run", action="store_true",
                        help="start from an empty feed when none is published yet")
    return parser.parse_args(argv)


def _reason(error: Exception) -> str:
    if isinstance(error, sources.GitHubAuthError):
        return "token do GitHub rejeitado"
    if isinstance(error, gemini.AuthError):
        return str(error)
    return repr(error)[:REASON_CHARS]


def _work(feed: dict, now, fetch_sources: bool) -> str | None:
    """Checks the secrets and runs the step; returns the failure reason or None."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return "chave do Gemini ausente"
    if fetch_sources and not os.environ.get("GITHUB_TOKEN"):
        return "token do GitHub ausente"
    client = job.new_client(key, os.environ.get("GEMINI_MODEL") or gemini.DEFAULT_MODEL)
    try:
        reason = job.step(feed, client, now, fetch_sources=fetch_sources)
    except Exception as error:  # noqa: BLE001 - any failure must be recorded in the status
        log.exception("job failed")
        return _reason(error)
    return reason


def main(argv: list[str] | None = None) -> int:
    """Parses the command, updates the feed and returns the exit status.

    Args:
        argv: The arguments, without the program name; defaults to `sys.argv[1:]`.

    Returns:
        0 when the run succeeded or had nothing to do, 1 when it failed.
    """
    args = _parse(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        feed = store.load(args.feed, allow_missing=args.first_run)
    except store.StoreError as error:
        log.error("cannot load the feed, writing nothing: %s", error)
        return 1
    now = job.now()
    fetched_today = feed.get("last_fetch") == job.today(now)
    if args.command == "retry" and fetched_today and not feed["pending"]:
        log.info("nothing to do: fetched today and nothing pending")
        return 0
    fetch_sources = args.command == "run" or not fetched_today
    if args.command == "retry" and fetch_sources:
        log.info("no fetch today yet, doing a full run")
    reason = _work(feed, now, fetch_sources)
    if reason:
        log.error("run failed: %s", reason)
    feed_model.set_status(feed, reason is None, now.isoformat(), reason)
    feed["generated_at"] = now.isoformat()
    found = feed_model.problems(feed)
    if found:
        log.error("the new feed is invalid, writing nothing: %s", "; ".join(found[:3]))
        return 1
    path = store.save(feed, args.out)
    log.info("wrote %s", path)
    return 1 if reason else 0


if __name__ == "__main__":
    sys.exit(main())
