"""Collect today's trending GitHub repositories from two rankings.

Sources, in the order their repositories are listed:
  - Trendshift: the daily ranking, read from the JSON embedded in its home page.
  - GitHub Trending: the daily ranking of github.com/trending, all languages.

`fetch` returns the repositories not yet known, without enriching them; `enrich` adds what the
page shows: the GitHub numbers (stars, forks, issues, language, license, created and pushed
days), homepage, description, the first image of the README and up to README_CHARS of the README
itself. A field GitHub could not give is None. `GITHUB_TOKEN`, when set, authenticates the API.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

REPO_URL = re.compile(r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)")
# The names go into the prompt of an unattended run, so anything else is dropped.
VALID_NAME = re.compile(r"[\w.-]+/[\w.-]+")
NOT_OWNERS = {"orgs", "sponsors", "features", "topics", "collections", "marketplace", "apps"}
README_CHARS = 8000
# Markdown and HTML images, in the order they appear.
IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)|<img\b[^>]*?\bsrc=[\"']([^\"']+)", re.IGNORECASE)
# Most READMEs open with status badges or star charts, which make a useless thumbnail.
BADGE_HOSTS = {"img.shields.io", "shields.io", "badgen.net", "badge.fury.io", "codecov.io",
               "api.star-history.com", "starchart.cc"}
BLOB_URL = re.compile(r"^https?://github\.com/([\w.-]+/[\w.-]+)/blob/")


class GitHubAuthError(Exception):
    """The GitHub API rejected the token."""


class EnrichFailed(Exception):
    """The GitHub API could not give a repository's numbers (rate limit, outage, network)."""


def get(url: str, headers: dict | None = None) -> str:
    """Returns the body of a URL, decoded as text."""
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", **(headers or {})})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode()


def trendshift() -> list[dict]:
    """Returns the Trendshift daily ranking.

    Raises:
        ValueError: when the page holds no ranking.
    """
    page = get("https://trendshift.io/").replace('\\"', '"')
    matches = list(re.finditer(r'"rank":(\d+),"score":\d+,"full_name":"([^"]+)"', page))
    repos = []
    for match, after in zip(matches, matches[1:] + [None], strict=False):
        # The entry ends where the next one starts, so its star count is never the next one's.
        entry = page[match.end() : after.start() if after else match.end() + 2000]
        gained = re.search(r'"repository_stars_gained":(\d+)', entry)
        repos.append({"name": match[2], "source": "trendshift", "rank": int(match[1]),
                      "gained": int(gained[1]) if gained else None})
    if not repos:
        raise ValueError("no ranking found, the page format may have changed")
    repos.sort(key=lambda repo: repo["rank"])
    return repos


def github_trending() -> list[dict]:
    """Returns the GitHub Trending daily ranking.

    Raises:
        ValueError: when the page holds no ranking.
    """
    page = get("https://github.com/trending?since=daily")
    repos = []
    for row in page.split('<article class="Box-row">')[1:]:
        name = re.search(r'<h2 class="h3 lh-condensed">\s*<a [^>]*?href="/([^"]+)"', row)
        gained = re.search(r"([\d,]+) stars? today", row)
        if name:
            repos.append({"name": name[1], "source": "github", "rank": len(repos) + 1,
                          "gained": int(gained[1].replace(",", "")) if gained else None})
    if not repos:
        raise ValueError("no ranking found, the page format may have changed")
    return repos


def github_headers(accept: str = "application/vnd.github+json") -> dict:
    """Returns the GitHub API headers, with the token from GITHUB_TOKEN when set."""
    headers = {"Accept": accept}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def first_image(readme: str, name: str) -> str | None:
    """Returns the absolute URL of the first image in a README that is not a badge.

    Args:
        readme: the README text, Markdown or HTML.
        name: the repository's owner/repo, against which relative paths are resolved.

    Returns:
        The image URL, or None when the README has no image besides badges.
    """
    for match in IMAGE.finditer(readme):
        url = match[1] or match[2]
        parts = urllib.parse.urlparse(url)
        host, file = parts.netloc.lower(), parts.path.lower().rsplit("/", 1)[-1]
        # Only the file name: a repo named "badger" must keep its images.
        if host in BADGE_HOSTS or "badge" in file or file.endswith(".svg"):
            continue
        if url.startswith("//"):
            return f"https:{url}"
        if host:
            return BLOB_URL.sub(r"https://raw.githubusercontent.com/\1/", url)
        if url.startswith(("data:", "#")):
            continue
        path = url.removeprefix("./").lstrip("/")
        return f"https://raw.githubusercontent.com/{name}/HEAD/{path}"
    return None


def enrich(repo: dict) -> dict:
    """Adds the GitHub numbers, the README and its first image to a fetched repository.

    A repository without a README gets an empty one. A repository whose numbers GitHub cannot
    give right now is not enriched at all, so it is not queued with null numbers.

    Args:
        repo: a repository from one of the sources, with at least "name".

    Returns:
        The same dict, with every field filled or None.

    Raises:
        GitHubAuthError: when the API answers HTTP 401, meaning the token is rejected.
        EnrichFailed: when the repository API call fails in any other way.
    """
    api = f"https://api.github.com/repos/{repo['name']}"
    try:
        info = json.loads(get(api, github_headers()))
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise GitHubAuthError(f"GitHub rejected the token: {error}") from error
        raise EnrichFailed(f"{repo['name']}: {error}") from error
    except Exception as error:  # noqa: BLE001 - rate limits and outages come in many shapes
        raise EnrichFailed(f"{repo['name']}: {error!r}") from error
    if not isinstance(info, dict):
        raise EnrichFailed(f"{repo['name']}: unexpected API answer")
    try:
        readme = get(f"{api}/readme", github_headers("application/vnd.github.raw"))
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise GitHubAuthError(f"GitHub rejected the token: {error}") from error
        readme = ""
    except Exception:  # noqa: BLE001
        readme = ""
    license_id = (info.get("license") or {}).get("spdx_id")
    repo.update({
        "url": f"https://github.com/{repo['name']}",
        "description": info.get("description") or repo.get("description"),
        "homepage": info.get("homepage") or None,
        "stars": info.get("stargazers_count"),
        "forks": info.get("forks_count"),
        "issues": info.get("open_issues_count"),
        "language": info.get("language"),
        "license": license_id if license_id != "NOASSERTION" else None,
        "created": (info.get("created_at") or "")[:10] or None,
        "pushed": (info.get("pushed_at") or "")[:10] or None,
        "image": first_image(readme, repo["name"]),
        "readme": readme[:README_CHARS],
    })
    for field in ("source", "rank", "gained"):
        repo.setdefault(field, None)
    return repo


def fetch(known: set[str]) -> tuple[list[dict], list[dict]]:
    """Returns the repositories on today's rankings that are not in `known`.

    Args:
        known: lowercase owner/repo names already published.

    Returns:
        A tuple `(repos, failed)`. `repos` lists Trendshift first, then GitHub, deduplicated
        case-insensitively, with valid names only and not enriched. `failed` holds one
        `{"source", "error"}` per source that could not be read.
    """
    seen = set(known)
    repos, failed = [], []
    # The lambdas look the functions up at call time, so tests can replace them.
    for label, source in (("trendshift", lambda: trendshift()),
                          ("github", lambda: github_trending())):
        # One source down must not hide the others.
        try:
            found = source()
        except Exception as error:  # noqa: BLE001
            failed.append({"source": label, "error": repr(error)})
            continue
        for repo in found:
            if VALID_NAME.fullmatch(repo["name"]) and repo["name"].lower() not in seen:
                seen.add(repo["name"].lower())
                repos.append(repo)
    return repos, failed
