from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import parse_qs, unquote, urlparse

URL_RE = re.compile(r"https?://[^\s<>\]\[\"']+", re.IGNORECASE)
PROJECT_RE = re.compile(r"^/gallery/\d+(?:/|$)", re.IGNORECASE)
REDIRECT_QUERY_KEYS = ("u", "url", "target", "redirect", "redirect_uri", "href")


def clean_url(url: str) -> str:
    return url.rstrip(".,;:!?)]}»")


def extract_urls(text: str, entity_urls: Iterable[str] = ()) -> list[str]:
    candidates = [*entity_urls, *URL_RE.findall(text or "")]
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        url = clean_url(candidate)
        if url and url not in seen:
            seen.add(url)
            unique.append(url)
    return sorted(
        unique, key=lambda value: (not is_behance_project(value), unique.index(value))
    )


def is_behance_host(url: str) -> bool:
    host = urlparse(url).hostname or ""
    host = host.lower().removeprefix("www.")
    return host == "behance.net" or host.endswith(".behance.net") or host == "be.net"


def is_behance_project(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return (host == "behance.net" or host.endswith(".behance.net")) and bool(
        PROJECT_RE.match(parsed.path)
    )


def is_facebook_url(url: str) -> bool:
    """Return True for Facebook pages and Facebook redirector links."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host == "facebook.com" or host.endswith(".facebook.com")


def expand_redirect_url(url: str) -> list[str]:
    """Return URLs embedded in redirectors such as Facebook's l.php."""
    queue = [clean_url(url)]
    expanded: list[str] = []
    seen: set[str] = set()
    while queue and len(seen) < 30:
        candidate = queue.pop(0)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        expanded.append(candidate)

        decoded = unquote(candidate)
        if decoded != candidate:
            queue.append(decoded)

        parsed = urlparse(candidate)
        for key, values in parse_qs(parsed.query).items():
            if key.lower() in REDIRECT_QUERY_KEYS:
                queue.extend(values)
    return expanded
