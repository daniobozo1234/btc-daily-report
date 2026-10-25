from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
from bs4 import BeautifulSoup

from btc_report.http_client import get_text


@dataclass
class Article:
    title: str
    link: str
    summary: str
    source: str
    lang: str
    published_at: str | None
    category: str  # crypto_pl | crypto_en | macro
    title_pl: str | None = None
    summary_pl: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def strip_html(text: str) -> str:
    if not text:
        return ""
    soup = BeautifulSoup(text, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    cleaned = soup.get_text(" ", strip=True)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _clip(text: str, limit: int) -> str:
    text = strip_html(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _parse_date(entry: dict) -> str | None:
    for key in ("published", "updated"):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            continue
    return None


def fetch_feed(name: str, url: str, lang: str, category: str, limit: int = 10) -> list[Article]:
    try:
        body = get_text(url)
        parsed = feedparser.parse(body)
    except Exception:
        parsed = feedparser.parse(url)

    articles: list[Article] = []
    for entry in parsed.entries[:limit]:
        title = _clip(getattr(entry, "title", "") or "(bez tytułu)", 180)
        link = getattr(entry, "link", "") or ""
        raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
        # Prefer content:encoded if present
        if hasattr(entry, "content") and entry.content:
            try:
                raw_summary = entry.content[0].value or raw_summary
            except Exception:
                pass
        summary = _clip(raw_summary, 500)
        if not link:
            continue
        articles.append(
            Article(
                title=title,
                link=link,
                summary=summary,
                source=name,
                lang=lang,
                published_at=_parse_date(entry),
                category=category,
                title_pl=title if lang == "pl" else None,
                summary_pl=summary if lang == "pl" and len(summary) > 40 else None,
            )
        )
    return articles


def collect_articles(config: dict) -> dict[str, list[Article]]:
    limits = config.get("limits") or {}
    n_crypto = int(limits.get("crypto_articles", 14))
    n_macro = int(limits.get("macro_articles", 6))
    per_feed = int(limits.get("per_feed", 5))

    pl: list[Article] = []
    for src in config.get("rss_crypto_pl") or []:
        pl.extend(fetch_feed(src["name"], src["url"], src.get("lang", "pl"), "crypto_pl", limit=per_feed))
    en: list[Article] = []
    for src in config.get("rss_crypto_en") or []:
        en.extend(fetch_feed(src["name"], src["url"], src.get("lang", "en"), "crypto_en", limit=per_feed))
    macro: list[Article] = []
    for src in config.get("rss_macro") or []:
        macro.extend(fetch_feed(src["name"], src["url"], src.get("lang", "pl"), "macro", limit=4))

    def sort_key(a: Article) -> str:
        return a.published_at or ""

    # Dedup po linku, potem bierz najnowsze; dbaj o różnorodność źródeł
    pl = _diversify(sorted(pl, key=sort_key, reverse=True), n_crypto)
    en = _diversify(sorted(en, key=sort_key, reverse=True), n_crypto)
    macro = _dedupe(sorted(macro, key=sort_key, reverse=True))[:n_macro]
    return {"crypto_pl": pl, "crypto_en": en, "macro": macro}


def _dedupe(items: list[Article]) -> list[Article]:
    seen: set[str] = set()
    out: list[Article] = []
    for a in items:
        if a.link in seen:
            continue
        seen.add(a.link)
        out.append(a)
    return out


def _diversify(items: list[Article], limit: int) -> list[Article]:
    """Prefer mix of sources so one portal doesn't dominate."""
    items = _dedupe(items)
    if len(items) <= limit:
        return items
    by_source: dict[str, list[Article]] = {}
    for a in items:
        by_source.setdefault(a.source, []).append(a)
    out: list[Article] = []
    # round-robin across sources
    while len(out) < limit:
        progressed = False
        for src in list(by_source.keys()):
            bucket = by_source[src]
            if not bucket:
                continue
            out.append(bucket.pop(0))
            progressed = True
            if len(out) >= limit:
                break
        if not progressed:
            break
    return out
