from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from btc_report.http_client import get_json, get_text

# BitcoinTalk często blokuje IP runnerów GitHub Actions — Lemmy jako zapas.
DEFAULT_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_LEMMY_COMMUNITIES = ("bitcoin", "cryptocurrency")


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


def _normalize_btalk_link(link: str) -> str:
    """topic=123.new → topic=123.0 (pierwsza strona wątku)."""
    return re.sub(r"(topic=\d+)\.(?:new|\d+)", r"\1.0", link)


def fetch_thread_excerpt(url: str, user_agent: str) -> str:
    try:
        html = get_text(url, headers={"User-Agent": user_agent}, timeout=20.0)
    except Exception:
        return ""
    soup = BeautifulSoup(html, "lxml")
    post = (
        soup.select_one(".post")
        or soup.select_one("#quickreplypreview")
        or soup.select_one("div.windowbg .post")
    )
    if not post:
        blocks = [div.get_text(" ", strip=True) for div in soup.select("div")[:40]]
        text = max(blocks, key=len) if blocks else ""
    else:
        text = post.get_text(" ", strip=True)
    return _clean(text, 650)


def fetch_bitcointalk(board_url: str, user_agent: str, limit: int = 8) -> list[ForumThread]:
    """Parse public BitcoinTalk board topic list + first-post excerpts."""
    ua = user_agent or DEFAULT_BROWSER_UA
    try:
        html = get_text(board_url, headers={"User-Agent": ua})
    except Exception as exc:
        print(f"  ! BitcoinTalk niedostępne: {exc}")
        return []

    soup = BeautifulSoup(html, "lxml")
    # challenge / soft-block pages usually have almost no topic links
    topic_anchors = soup.select("a[href*='topic=']")
    if len(topic_anchors) < 3:
        title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:80]
        print(
            f"  ! BitcoinTalk: podejrzanie mało linków topic= ({len(topic_anchors)}); "
            f"title={title!r} — prawdopodobnie blokada IP."
        )
        return []

    threads: list[ForumThread] = []
    for a in soup.select("span.subject a[href*='topic='], td.subject a[href*='topic='], a[href*='topic=']"):
        title = " ".join(a.get_text(" ", strip=True).split())
        href = a.get("href") or ""
        if not title or len(title) < 8:
            continue
        lower = title.lower()
        if lower.startswith(("next", "prev", "go up", "re:")):
            continue
        if "new replies" in lower or "pages:" in lower:
            continue
        link = _normalize_btalk_link(urljoin(board_url, href.split("#")[0]))
        if "topic=" not in link:
            continue
        if any(t.link == link for t in threads):
            continue
        threads.append(ForumThread(title=title, link=link, replies=None, source="BitcoinTalk"))
        if len(threads) >= limit:
            break

    for t in threads:
        t.excerpt = fetch_thread_excerpt(t.link, ua) or None

    print(f"  · BitcoinTalk: {len(threads)} wątków")
    return threads


def fetch_lemmy(
    communities: list[str] | tuple[str, ...] | None = None,
    *,
    instance: str = "https://lemmy.world",
    limit: int = 8,
) -> list[ForumThread]:
    """Publiczne posty z Lemmy (działa z IP datacenter, w przeciwieństwie do BTalk/Reddit)."""
    communities = list(communities or DEFAULT_LEMMY_COMMUNITIES)
    base = instance.rstrip("/")
    out: list[ForumThread] = []
    seen: set[str] = set()
    per = max(3, (limit + len(communities) - 1) // max(len(communities), 1))

    for community in communities:
        if len(out) >= limit:
            break
        url = f"{base}/api/v3/post/list"
        try:
            data = get_json(
                url,
                params={
                    "community_name": community,
                    "sort": "Active",
                    "limit": per + 2,
                    "type_": "All",
                },
                headers={"User-Agent": "btc-daily-report/1.0 (+personal research)"},
                timeout=20.0,
            )
        except Exception as exc:
            print(f"  ! Lemmy {community}: {exc}")
            continue
        for item in data.get("posts") or []:
            if len(out) >= limit:
                break
            post = item.get("post") or {}
            title = " ".join((post.get("name") or "").split())
            link = post.get("ap_id") or post.get("url") or ""
            if not title or not link or link in seen:
                continue
            # pomijaj czysty spam/reklamy bez treści
            body = post.get("body") or ""
            if post.get("deleted") or post.get("removed"):
                continue
            seen.add(link)
            out.append(
                ForumThread(
                    title=title,
                    link=link,
                    replies=str((item.get("counts") or {}).get("comments") or "") or None,
                    source=f"Lemmy/{community}",
                    excerpt=_clean(body, 650) or None,
                )
            )

    print(f"  · Lemmy: {len(out)} postów ({', '.join(communities)})")
    return out


def fetch_forum_threads(
    *,
    board_url: str,
    user_agent: str,
    limit: int = 8,
    lemmy_communities: list[str] | None = None,
    lemmy_instance: str = "https://lemmy.world",
) -> list[ForumThread]:
    """BitcoinTalk najpierw; gdy pusto (typowe na Actions) — Lemmy."""
    threads = fetch_bitcointalk(board_url, user_agent or DEFAULT_BROWSER_UA, limit=limit)
    if len(threads) >= max(3, limit // 2):
        return threads
    print("  · BitcoinTalk słabo/pusto — dociągam Lemmy jako zapas forów…")
    lemmy = fetch_lemmy(
        lemmy_communities or DEFAULT_LEMMY_COMMUNITIES,
        instance=lemmy_instance,
        limit=limit,
    )
    # doklej unikalne z BTalk jeśli coś jednak przyszło
    seen = {t.link for t in lemmy}
    for t in threads:
        if t.link not in seen and len(lemmy) < limit:
            lemmy.append(t)
            seen.add(t.link)
    return lemmy[:limit]
