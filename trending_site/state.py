"""File-backed state of the daily job: seen repos, retry queue, day pages and last status.

Layout under the root: `seen.json`, `queue.json`, `status.json` and `days/<date>.json`. A missing
file reads as empty; an unreadable or invalid one raises `StateError` rather than being treated
as empty, so a corrupt file can never make repos look unseen. Writes are atomic.
"""

import json
import pathlib
from collections.abc import Iterable

from trending_site import files


class StateError(Exception):
    """A state file could not be read or holds invalid JSON."""


class State:
    """The state directory of the job.

    Args:
        root: directory holding the state files; created on first write.
    """

    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root)

    def _read(self, path: pathlib.Path, default):
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return default
        except (OSError, UnicodeDecodeError) as error:
            raise StateError(f"cannot read {path}: {error}") from error
        try:
            return json.loads(text)
        except ValueError as error:
            raise StateError(f"invalid JSON in {path}: {error}") from error

    def _write(self, path: pathlib.Path, data) -> None:
        files.atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2), 0o600)

    def seen(self) -> dict[str, str]:
        """Return `{"owner/repo": "first seen day"}`."""
        return self._read(self.root / "seen.json", {})

    def add_seen(self, names: Iterable[str], day: str) -> None:
        """Mark names as seen on `day`; a name already seen keeps its first day."""
        seen = self.seen()
        known = {name.lower() for name in seen}
        for name in names:
            if name.lower() not in known:
                seen[name] = day
                known.add(name.lower())
        self._write(self.root / "seen.json", seen)

    def queue(self) -> list[dict]:
        """Return the queued entries, oldest first."""
        return self._read(self.root / "queue.json", [])

    def save_queue(self, entries: list[dict]) -> None:
        """Replace the queue."""
        self._write(self.root / "queue.json", entries)

    def day(self, date: str) -> dict | None:
        """Return the page data saved for `date`, or None."""
        return self._read(self.root / "days" / f"{date}.json", None)

    def save_day(self, page: dict) -> None:
        """Save page data under its `date`."""
        self._write(self.root / "days" / f"{page['date']}.json", page)

    def days(self) -> list[str]:
        """Return the dates with a saved page, newest first."""
        folder = self.root / "days"
        if not folder.is_dir():
            return []
        return sorted((path.stem for path in folder.glob("*.json")), reverse=True)

    def status(self) -> dict | None:
        """Return the last run status `{"ok", "at", "reason"}`, or None."""
        return self._read(self.root / "status.json", None)

    def save_status(self, ok: bool, at: str, reason: str | None) -> None:
        """Record the outcome of a run."""
        self._write(self.root / "status.json", {"ok": ok, "at": at, "reason": reason})

    def reconcile(self) -> None:
        """Make every repo on a saved day page seen and absent from the queue.

        A repo is recorded with the earliest page date that lists it. Names compare
        case-insensitively. Repairs a run that saved a page but died before updating the rest.
        """
        published: dict[str, tuple[str, str]] = {}
        for date in sorted(self.days()):
            for repo in (self.day(date) or {}).get("repos", []):
                published.setdefault(repo["name"].lower(), (repo["name"], date))
        if not published:
            return
        seen = self.seen()
        known = {name.lower() for name in seen}
        for key, (name, date) in published.items():
            if key not in known:
                seen[name] = date
        self._write(self.root / "seen.json", seen)
        queue = self.queue()
        kept = [item for item in queue if item["repo"]["name"].lower() not in published]
        if len(kept) != len(queue):
            self.save_queue(kept)
