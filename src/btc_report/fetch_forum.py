from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from btc_report.http_client import get_text


@dataclass
class ForumThread:
    title: str
    link: str
    replies: str | None
    source: str
    excerpt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(text: str, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def fetch_thread_excerpt(url: str, user_agent: str) -> str:
    try:
        html = get_text(url, headers={"User-Agent": user_agent}, timeout=20.0)
    except Exception:
        return ""
    soup = BeautifulSoup(html, "lxml")
    # BitcoinTalk classic: first post body
    post = soup.select_one(".post") or soup.select_one("#quickreplypreview") or soup.select_one("div.windowbg .post")
    if not post:
        # fallback: largest text block
        blocks = [div.get_text(" ", strip=True) for div in soup.select("div")[:40]]
        text = max(blocks, key=len) if blocks else ""
    else:
        text = post.get_text(" ", strip=True)
    return _clean(text, 650)


def fetch_bitcointalk(board_url: str, user_agent: str, limit: int = 8) -> list[ForumThread]:
    """Parse public BitcoinTalk board topic list + first-post excerpts."""
    try:
        html = get_text(board_url, headers={"User-Agent": user_agent})
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    threads: list[ForumThread] = []

    for a in soup.select("span.subject a[href*='topic='], a[href*='topic=']"):
        title = " ".join(a.get_text(" ", strip=True).split())
        href = a.get("href") or ""
        if not title or len(title) < 8:
            continue
        lower = title.lower()
        if lower.startswith(("next", "prev", "go up", "re:")):
            continue
        if "new replies" in lower or "pages:" in lower:
            continue
        link = urljoin(board_url, href.split("#")[0])
        # normalize to first page of topic
        if "topic=" not in link:
            continue
        if any(t.link == link for t in threads):
            continue
        threads.append(ForumThread(title=title, link=link, replies=None, source="BitcoinTalk"))
        if len(threads) >= limit:
            break

    for t in threads:
        t.excerpt = fetch_thread_excerpt(t.link, user_agent) or None

    return threads
