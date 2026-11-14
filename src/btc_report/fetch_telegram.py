from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup

from btc_report.http_client import get_text

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Wiodące, publiczne kanały z działającym podglądem t.me/s/ (bez konta Telegram).
# Świadomie pomijamy: airdropy/miniapps, fałszywe „CoinDesk”, zablokowane podglądy (Whale Alert).
DEFAULT_CHANNELS: list[dict[str, str]] = [
    {"handle": "cointelegraph", "name": "Cointelegraph"},
    {"handle": "WatcherGuru", "name": "Watcher Guru"},
    {"handle": "Bitcoin", "name": "Bitcoin"},
    {"handle": "glassnode", "name": "Glassnode"},
    {"handle": "cryptoquant_official", "name": "CryptoQuant"},
    {"handle": "TreeNewsFeed", "name": "Tree News"},
    {"handle": "wublockchainenglish", "name": "Wu Blockchain"},
    {"handle": "CoinBureau", "name": "Coin Bureau"},
]


@dataclass
class TelegramPost:
    channel: str
    handle: str
    link: str
    text: str
    published_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(text: str, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_channel_posts(
    handle: str,
    *,
    name: str | None = None,
    user_agent: str = DEFAULT_UA,
    per_channel: int = 2,
    max_age_hours: int = 72,
) -> list[TelegramPost]:
    """Publiczny podgląd https://t.me/s/<handle> — bez logowania / Bot API."""
    handle = handle.lstrip("@").strip()
    if not handle:
        return []
    url = f"https://t.me/s/{handle}"
    try:
        html = get_text(
            url,
            headers={
                "User-Agent": user_agent or DEFAULT_UA,
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=20.0,
        )
    except Exception as exc:
        print(f"  ! Telegram @{handle}: {exc}")
        return []

    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one(".tgme_channel_info_header_title")
    channel_name = name or (
        title_el.get_text(" ", strip=True) if title_el else handle
    )
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    posts: list[TelegramPost] = []

    for msg in soup.select("div.tgme_widget_message"):
        text_el = msg.select_one("div.tgme_widget_message_text")
        if not text_el:
            continue
        text = _clean(text_el.get_text(" ", strip=True), 520)
        if len(text) < 40:
            continue
        time_el = msg.select_one("time")
        published = _parse_dt(time_el.get("datetime") if time_el else None)
        # bez daty / za stare — nie wpuszczamy (wcześniej undated przechodziły)
        if not published or published < cutoff:
            continue
        date_a = msg.select_one("a.tgme_widget_message_date")
        link = (date_a.get("href") if date_a else None) or msg.get("data-post")
        if link and not str(link).startswith("http"):
            link = f"https://t.me/{link}"
        if not link:
            link = url
        posts.append(
            TelegramPost(
                channel=channel_name,
                handle=handle,
                link=str(link),
                text=text,
                published_at=published.isoformat() if published else None,
            )
        )

    # najnowsze na końcu DOM — bierzemy od końca
    posts = posts[-per_channel:] if posts else []
    return list(reversed(posts))


def fetch_telegram_posts(
    channels: list[dict[str, str]] | None = None,
    *,
    user_agent: str = DEFAULT_UA,
    per_channel: int = 2,
    max_age_hours: int = 72,
    limit: int = 14,
) -> list[TelegramPost]:
    channels = channels or DEFAULT_CHANNELS
    out: list[TelegramPost] = []
    seen: set[str] = set()
    for ch in channels:
        if len(out) >= limit:
            break
        handle = (ch.get("handle") or ch.get("username") or "").strip()
        name = (ch.get("name") or "").strip() or None
        got = fetch_channel_posts(
            handle,
            name=name,
            user_agent=user_agent,
            per_channel=per_channel,
            max_age_hours=max_age_hours,
        )
        print(f"  · TG @{handle}: {len(got)} postów (≤{max_age_hours}h)")
        for p in got:
            if p.link in seen:
                continue
            seen.add(p.link)
            out.append(p)
            if len(out) >= limit:
                break
    # świeższe najpierw
    out.sort(key=lambda p: p.published_at or "", reverse=True)
    print(f"  łącznie postów Telegram: {len(out)}")
    return out[:limit]
