"""Renders the day pages and the index from the HTML template."""

import json
import pathlib

from trending_site import files

PLACEHOLDER = "/*__DATA__*/null"
TEMPLATE = pathlib.Path(__file__).parent / "assets" / "page.html"


def _html(data: dict) -> str:
    template = TEMPLATE.read_text("utf-8")
    # No "<" at all in the injected data: "</script>" would close the page's script tag and
    # "<!--<script" would keep the real closing tag from ending it.
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    return template.replace(PLACEHOLDER, payload)


def day_html(page: dict) -> str:
    """Renders one day's page; the page links back to the index."""
    return _html({"page": page, "days": None, "status": None})


def index_html(latest: dict | None, days: list[str], status: dict | None) -> str:
    """Renders the index: the latest page, the list of days and a failed-run banner.

    Args:
        latest: The most recent day page data, or None when nothing was published.
        days: Published dates (YYYY-MM-DD).
        status: The last run status, or None when unknown.
    """
    return _html({"page": latest, "days": days, "status": status})


def write(site_dir: pathlib.Path, name: str, html: str) -> None:
    """Writes `html` to `site_dir/name` atomically, mode 0644."""
    files.atomic_write(site_dir / name, html, 0o644)
