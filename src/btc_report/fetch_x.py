from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser

from btc_report.http_client import get_text

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Publiczne profile przez FxTwitter RSS — BEZ konta X / BEZ płatnego API.
# Lista zweryfikowana empirycznie (część handle'i zwraca 404 — wtedy skip).
DEFAULT_ACCOUNTS: list[dict[str, str]] = [
    {"handle": "saylor", "name": "Michael Saylor"},
    {"handle": "DocumentingBTC", "name": "Documenting Bitcoin"},
    {"handle": "APompliano", "name": "Anthony Pompliano"},
    {"handle": "CryptoHayes", "name": "Arthur Hayes"},
    {"handle": "adam3us", "name": "Adam Back"},
    {"handle": "lopp", "name": "Jameson Lopp"},
    {"handle": "LynAldenContact", "name": "Lyn Alden"},
    {"handle": "Excellion", "name": "Samson Mow"},
    {"handle": "whale_alert", "name": "Whale Alert"},
    {"handle": "coinbase", "name": "Coinbase"},
    {"handle": "Tier10k", "name": "Tier10k"},
    {"handle": "VitalikButerin", "name": "Vitalik Buterin"},
]


@dataclass
class XPost:
    author: str
    handle: str
    link: str
    text: str
    published_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(text: str, limit: int = 420) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _parse_rss_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw_s = str(raw).strip()
    try:
        dt = datetime.fromisoformat(raw_s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        dt = parsedate_to_datetime(raw_s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def fetch_account_posts(
    handle: str,
    *,
    name: str | None = None,
    user_agent: str = DEFAULT_UA,
    per_account: int = 2,
    max_age_hours: int = 72,
) -> list[XPost]:
    """RSS: https://fxtwitter.com/<handle>/feed.xml — bez logowania."""
    handle = handle.lstrip("@").strip()
    if not handle:
        return []
    url = f"https://fxtwitter.com/{handle}/feed.xml"
    try:
        body = get_text(
            url,
            headers={
                "User-Agent": user_agent or DEFAULT_UA,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
            timeout=18.0,
        )
    except Exception as exc:
        print(f"  ! X @{handle}: {exc}")
        return []

    parsed = feedparser.parse(body)
    if not parsed.entries:
        print(f"  · X @{handle}: brak feedu / 0 wpisów")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    author = name or handle
    out: list[XPost] = []
    for entry in parsed.entries[: max(per_account * 4, 8)]:
        text = _clean(getattr(entry, "title", "") or getattr(entry, "summary", "") or "")
        if len(text) < 20:
            continue
        published = _parse_rss_date(getattr(entry, "published", None) or getattr(entry, "updated", None))
        if published and published < cutoff:
            continue
        link = getattr(entry, "link", "") or ""
        if not link:
            # czasem id w entry.id
            eid = getattr(entry, "id", "") or ""
            if "status" in eid or "x.com" in eid or "twitter.com" in eid:
                link = eid
            else:
                link = f"https://x.com/{handle}"
        # normalizuj do x.com
        link = link.replace("https://twitter.com/", "https://x.com/").replace(
            "http://twitter.com/", "https://x.com/"
        )
        out.append(
            XPost(
                author=author,
                handle=handle,
                link=link,
                text=text,
                published_at=published.isoformat() if published else None,
            )
        )
        if len(out) >= per_account:
            break
    print(f"  · X @{handle}: {len(out)} postów (≤{max_age_hours}h)")
    return out


def fetch_x_posts(
    accounts: list[dict[str, str]] | None = None,
    *,
    user_agent: str = DEFAULT_UA,
    per_account: int = 2,
    max_age_hours: int = 72,
    limit: int = 12,
) -> list[XPost]:
    accounts = accounts or DEFAULT_ACCOUNTS
    out: list[XPost] = []
    seen: set[str] = set()
    for acc in accounts:
        if len(out) >= limit:
            break
        handle = (acc.get("handle") or acc.get("username") or "").strip()
        name = (acc.get("name") or "").strip() or None
        got = fetch_account_posts(
            handle,
            name=name,
            user_agent=user_agent,
            per_account=per_account,
            max_age_hours=max_age_hours,
        )
        for p in got:
            key = p.link or f"{p.handle}:{p.text[:40]}"
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
            if len(out) >= limit:
                break
    out.sort(key=lambda p: p.published_at or "", reverse=True)
    print(f"  łącznie postów X: {len(out)}")
    return out[:limit]
