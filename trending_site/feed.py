"""The public feed: its shape, its checks and the changes the daily job makes to it."""

from datetime import date, timedelta

WINDOW_DAYS = 7


def empty() -> dict:
    """Returns a feed with no days and nothing pending."""
    return {"version": 1, "generated_at": None, "last_fetch": None, "status": None,
            "days": [], "pending": []}


def _is_str(value: object) -> bool:
    return isinstance(value, str)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _repo_problems(repo: object, where: str) -> list[str]:
    if not isinstance(repo, dict):
        return [f"{where} repo is not an object"]
    out = []
    if not _is_str(repo.get("name")):
        out.append(f"{where} repo without a string name")
    if repo.get("stars") is not None and not _is_int(repo.get("stars")):
        out.append(f"{where} repo stars is not an int or null")
    return out


def _day_problems(entry: object) -> list[str]:
    if not isinstance(entry, dict):
        return ["day is not an object"]
    out = []
    if not _is_date(entry.get("date")):
        out.append("day date is not an ISO date")
    failed = entry.get("failed")
    if not isinstance(failed, list):
        out.append("day failed is not a list")
    else:
        for item in failed:
            if not (isinstance(item, dict) and _is_str(item.get("source"))
                    and _is_str(item.get("error"))):
                out.append("day failed entry needs string source and error")
    repos = entry.get("repos")
    if not isinstance(repos, list):
        out.append("day repos is not a list")
    else:
        for repo in repos:
            out.extend(_repo_problems(repo, "day"))
            if isinstance(repo, dict):
                if "readme" in repo:
                    out.append("day repo has a readme")
                if repo.get("summary") is not None and not isinstance(repo.get("summary"), dict):
                    out.append("day repo summary is not an object or null")
    return out


def _pending_problems(entry: object) -> list[str]:
    if not isinstance(entry, dict):
        return ["pending entry is not an object"]
    out = _repo_problems(entry.get("repo"), "pending")
    if not _is_date(entry.get("queued")):
        out.append("pending queued is not an ISO date")
    if not _is_int(entry.get("failures")) or entry["failures"] < 0:
        out.append("pending failures is not an int >= 0")
    if entry.get("last_failure") is not None and not _is_date(entry.get("last_failure")):
        out.append("pending last_failure is not an ISO date or null")
    return out


def _status_problems(status: object) -> list[str]:
    if status is None:
        return []
    if not isinstance(status, dict):
        return ["status is not an object or null"]
    out = []
    if not isinstance(status.get("ok"), bool):
        out.append("status ok is not a bool")
    if not _is_str(status.get("at")):
        out.append("status at is not a string")
    if status.get("reason") is not None and not _is_str(status.get("reason")):
        out.append("status reason is not a string or null")
    return out


def _list_problems(feed: dict) -> list[str]:
    out = []
    for key, check in (("days", _day_problems), ("pending", _pending_problems)):
        entries = feed.get(key)
        if not isinstance(entries, list):
            out.append(f"{key} is not a list")
        else:
            out.extend(p for entry in entries for p in check(entry))
    return out


def _invariant_problems(feed: dict) -> list[str]:
    out = []
    dates = [d["date"] for d in feed["days"]]
    if len(set(dates)) != len(dates):
        out.append("days repeat a date")
    if dates != sorted(dates, reverse=True):
        out.append("days are not newest first")
    names = [r["name"].lower() for d in feed["days"] for r in d["repos"]]
    names += [e["repo"]["name"].lower() for e in feed["pending"]]
    if len(set(names)) != len(names):
        out.append("a repo appears more than once")
    return out


def problems(feed: object) -> list[str]:
    """Returns what is wrong with a feed, enough to keep it from being published.

    Covers the type of every field, ISO dates, the absence of READMEs in days, newest-first
    unique days and repos listed at most once, case-insensitively. Empty when valid.
    """
    if not isinstance(feed, dict):
        return ["feed is not an object"]
    out = []
    if not _is_int(feed.get("version")) or feed["version"] != 1:
        out.append("unsupported version")
    if feed.get("generated_at") is not None and not _is_str(feed.get("generated_at")):
        out.append("generated_at is not a string or null")
    if feed.get("last_fetch") is not None and not _is_date(feed.get("last_fetch")):
        out.append("last_fetch is not an ISO date or null")
    out.extend(_status_problems(feed.get("status")))
    found = _list_problems(feed)
    out.extend(found)
    if not found:
        out.extend(_invariant_problems(feed))
    return out


def known_names(feed: dict) -> set[str]:
    """Returns the lowercase names of every repo in the days and the pending queue."""
    names = {repo["name"].lower() for day in feed["days"] for repo in day["repos"]}
    return names | {entry["repo"]["name"].lower() for entry in feed["pending"]}


def add_pending(feed: dict, repos: list[dict], day: str) -> None:
    """Queues repos not yet known, case-insensitively, as pending on `day`."""
    seen = known_names(feed)
    for repo in repos:
        if repo["name"].lower() not in seen:
            seen.add(repo["name"].lower())
            feed["pending"].append(
                {"repo": repo, "queued": day, "failures": 0, "last_failure": None})


def publish(feed: dict, repos: list[dict], day: str, failed: list[dict] | None) -> None:
    """Adds repos, without their README, to the feed day `day`, sorted by stars.

    A missing day is created at its date-sorted position, with `failed` as its failed sources
    (none when `failed` is None). A day that exists keeps its earlier repos, a repo already
    there (same name, case-insensitively) is replaced, and its failed sources become those
    also failed in `failed`, or stay as they are when `failed` is None (a run that did not
    fetch). A repo appearing on another day or in the pending queue is the caller's job:
    remove it from pending when publishing it.
    """
    entry = next((d for d in feed["days"] if d["date"] == day), None)
    if entry is None:
        entry = {"date": day, "failed": list(failed or []), "repos": []}
        position = next((i for i, d in enumerate(feed["days"]) if d["date"] < day),
                        len(feed["days"]))
        feed["days"].insert(position, entry)
    elif failed is not None:
        sources = {item["source"] for item in failed}
        entry["failed"] = [item for item in entry["failed"] if item["source"] in sources]
    new = {r["name"].lower(): {k: v for k, v in r.items() if k != "readme"} for r in repos}
    kept = [r for r in entry["repos"] if r["name"].lower() not in new]
    entry["repos"] = kept + list(new.values())
    entry["repos"].sort(key=lambda r: (r["stars"] is None, -(r["stars"] or 0)))


def prune(feed: dict, day: str) -> None:
    """Drops the days older than the window that ends on `day`."""
    oldest = date.fromisoformat(day) - timedelta(days=WINDOW_DAYS - 1)
    kept = [d for d in feed["days"] if date.fromisoformat(d["date"]) >= oldest]
    feed["days"] = sorted(kept, key=lambda d: date.fromisoformat(d["date"]), reverse=True)


def set_status(feed: dict, ok: bool, at: str, reason: str | None) -> None:
    """Records the outcome of the latest run."""
    feed["status"] = {"ok": ok, "at": at, "reason": reason}
