"""Loading the published feed and saving the new one."""

import http.client
import json
import os
import pathlib
import tempfile
import time
import urllib.error
import urllib.request

from trending_site import feed as feed_model

TIMEOUT = 30
USER_AGENT = "trending-repos-site"


class StoreError(Exception):
    """The feed could not be read or is invalid; it must not be published."""


def _default_get(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        try:
            return error.code, error.read()
        except (OSError, http.client.HTTPException):
            return error.code, b""
    except (urllib.error.URLError, TimeoutError, http.client.HTTPException, OSError) as error:
        raise StoreError(f"network error: {error}") from error


def _parse(raw: bytes | str) -> dict:
    try:
        feed = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise StoreError("invalid JSON") from error
    found = feed_model.problems(feed)
    if found:
        raise StoreError("invalid feed: " + "; ".join(found[:3]))
    return feed


def _missing(allow_missing: bool) -> dict:
    if not allow_missing:
        raise StoreError("feed not found (404)")
    return feed_model.empty()


def load(source: str, get=None, allow_missing: bool = False) -> dict:
    """Reads the feed from a URL or a local path.

    Args:
        source: An http(s) URL or a file path.
        get: Optional `get(url) -> (status, body)` used for URLs.
        allow_missing: Whether a missing feed (404 or missing file) is an empty feed, as on
            the first run; otherwise it is an error, so a lost feed never resets the history.

    Returns:
        The feed; an empty feed when it is missing and `allow_missing` is true.

    Raises:
        StoreError: On a network error, a non-200 status, a missing feed (unless allowed),
            invalid JSON or an invalid feed.
    """
    if source.startswith(("http://", "https://")):
        separator = "&" if "?" in source else "?"
        status, body = (get or _default_get)(f"{source}{separator}t={int(time.time())}")
        if status == 404:
            return _missing(allow_missing)
        if status != 200:
            raise StoreError(f"unexpected HTTP status {status}")
        return _parse(body)
    path = pathlib.Path(source)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _missing(allow_missing)
    except OSError as error:
        raise StoreError(f"cannot read {path}: {error}") from error
    return _parse(raw)


def save(feed: dict, out_dir: pathlib.Path) -> pathlib.Path:
    """Writes the feed as compact JSON to `out_dir/feed.json` and returns that path.

    The JSON goes to a temporary file in `out_dir` first and then replaces `feed.json`, so an
    interrupted write never leaves a partial feed behind.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "feed.json"
    data = json.dumps(feed, ensure_ascii=False, separators=(",", ":"))
    tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=out_dir, prefix=".feed-",
                                      suffix=".tmp", delete=False)
    try:
        with tmp:
            tmp.write(data)
        os.chmod(tmp.name, 0o644)  # temporary files start private; the feed is public
        os.replace(tmp.name, path)
    except BaseException:
        os.unlink(tmp.name)
        raise
    return path
