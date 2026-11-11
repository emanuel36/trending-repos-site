"""Command line: `python3 -m trending_site run` runs the daily job once."""

import argparse
import datetime
import logging
import os
import sys

from trending_site import gemini, run


def main(argv: list[str] | None = None) -> int:
    """Parses the command, runs it and returns the exit status."""
    parser = argparse.ArgumentParser(prog="trending_site")
    parser.add_argument("command", choices=["run"], help="run the daily job once")
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    config, problem = run.config_from_env()
    now = datetime.datetime.now(run.TZ)
    if problem:
        return run.fail(config, now, problem)
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return run.fail(config, now, "chave do Gemini ausente")
    if not os.environ.get("GITHUB_TOKEN"):
        return run.fail(config, now, "token do GitHub ausente")
    client = gemini.Client(key, os.environ.get("GEMINI_MODEL") or gemini.DEFAULT_MODEL)
    return run.run(config, client, now)


if __name__ == "__main__":
    sys.exit(main())
