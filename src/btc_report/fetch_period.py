from __future__ import annotations

"""Kontekst pod raporty weekly/monthly/quarterly/yearly — nie kopia daily."""

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

import feedparser

from btc_report import store
from btc_report.fetch_market import fetch_ohlc
from btc_report.fetch_news import strip_html
from btc_report.http_client import get_text
from btc_report.periods import ReportWindow


def collect_period_context(win: ReportWindow) -> dict[str, Any]:
    """Newsy z okna + cudze recapy + trajektoria ceny (CoinGecko)."""
    days = max(1, (win.end.date() - win.start.date()).days + 1)
    price = _price_path(days=min(days, 365), start=win.start, end=win.end)
    archived = store.articles_in_window(win.start, win.end, limit=80)
    external = _fetch_period_headlines(win)
    wraps = [a for a in external if a.get("is_wrap")]
    headlines = [a for a in external if not a.get("is_wrap")]

    # dedupe po tytule/linku — archiwum lokalne + zewnętrzne
    merged = _dedupe([*archived, *headlines], limit=40)
    wrap_merged = _dedupe(wraps, limit=12)

    return {
        "days": days,
        "price_path": price,
        "archived_articles": archived[:40],
        "period_headlines": merged,
        "period_wraps": wrap_merged,
        "note": (
            "Źródła okresu: lokalna baza artykułów z raportów dziennych, "
            "Google News RSS (wydarzenia + cudze weekly/monthly wrap) oraz CoinGecko OHLC."
        ),
    }


def _price_path(*, days: int, start: datetime, end: datetime) -> dict[str, Any]:
    out: dict[str, Any] = {"samples": 0}
    try:
        btc = fetch_ohlc(days=days, coin_id="bitcoin")
        eth = fetch_ohlc(days=min(days, 365), coin_id="ethereum")
    except Exception as exc:
        out["note"] = f"Brak OHLC CoinGecko: {exc}"
        # fallback z SQLite
        out.update(store.period_market_summary(start, end))
        return out

    if btc is None or btc.empty:
        out.update(store.period_market_summary(start, end))
        return out

    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    btc = btc[(btc.index >= start_utc) & (btc.index <= end_utc)]
    if btc.empty:
        btc = fetch_ohlc(days=days, coin_id="bitcoin")

    b_open = float(btc["Open"].iloc[0])
    b_close = float(btc["Close"].iloc[-1])
    out.update(
        {
            "samples": int(len(btc)),
            "btc_start": b_open,
            "btc_end": b_close,
            "btc_min": float(btc["Low"].min()),
            "btc_max": float(btc["High"].max()),
            "btc_change_pct": ((b_close / b_open) - 1.0) * 100.0 if b_open else None,
            "source": "coingecko_ohlc",
        }
    )
    if eth is not None and not eth.empty:
        eth = eth[(eth.index >= start_utc) & (eth.index <= end_utc)]
        if not eth.empty:
            e_open = float(eth["Open"].iloc[0])
            e_close = float(eth["Close"].iloc[-1])
            out.update(
                {
                    "eth_start": e_open,
                    "eth_end": e_close,
                    "eth_min": float(eth["Low"].min()),
                    "eth_max": float(eth["High"].max()),
                    "eth_change_pct": ((e_close / e_open) - 1.0) * 100.0 if e_open else None,
                }
            )
    return out


def _fetch_period_headlines(win: ReportWindow) -> list[dict[str, Any]]:
    label = {
        "weekly": "week",
        "monthly": "month",
        "quarterly": "quarter",
        "yearly": "year",
    }.get(win.report_type, "month")
    when = _google_when(win)
    month_name = win.start.strftime("%B %Y")

    queries = [
        (f"bitcoin OR crypto OR ethereum when:{when}", False),
        (f"Binance hack OR crypto theft OR exploit when:{when}", False),
        (
            f'(crypto OR bitcoin) ("week in review" OR "monthly recap" OR "month in review" '
            f'OR wrap-up OR "monthly wrap" OR podsumowanie) when:{when}',
            True,
        ),
        (f"bitcoin {month_name} recap OR review OR podsumowanie", True),
    ]
    if win.report_type == "weekly":
        queries.append((f'bitcoin "this week" OR "week in review" when:{when}', True))

    out: list[dict[str, Any]] = []
    for q, is_wrap in queries:
        out.extend(_google_news_rss(q, is_wrap=is_wrap, limit=10))
    return out


def _google_when(win: ReportWindow) -> str:
    days = max(1, (win.end.date() - win.start.date()).days + 1)
    if days <= 8:
        return "7d"
    if days <= 35:
        return "1m"
    if days <= 100:
        return "3m"
    return "1y"


def _google_news_rss(query: str, *, is_wrap: bool, limit: int = 10) -> list[dict[str, Any]]:
    url = (
        "https://news.google.com/rss/search?q="
        + quote_plus(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        body = get_text(url, timeout=25.0)
        parsed = feedparser.parse(body)
    except Exception as exc:
        print(f"! Google News RSS pominięte ({query[:40]}…): {exc}")
        return []

    items: list[dict[str, Any]] = []
    for entry in parsed.entries[:limit]:
        title = strip_html(getattr(entry, "title", "") or "")
        link = getattr(entry, "link", "") or ""
        summary = strip_html(getattr(entry, "summary", "") or getattr(entry, "description", "") or "")
        published = None
        raw = getattr(entry, "published", None)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                published = dt.astimezone(timezone.utc).isoformat()
            except Exception:
                published = str(raw)
        source = ""
        try:
            source = entry.get("source", {}).get("title") or ""
        except Exception:
            source = ""
        if not title:
            continue
        items.append(
            {
                "title": title[:220],
                "link": link,
                "summary": summary[:400],
                "source": source or "Google News",
                "published_at": published,
                "is_wrap": is_wrap,
                "category": "period_wrap" if is_wrap else "period_news",
            }
        )
    return items


def _dedupe(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for a in items:
        key = (a.get("link") or "").split("?")[0] or re.sub(r"\W+", "", (a.get("title") or "").lower())[:80]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(a)
        if len(out) >= limit:
            break
    return out
