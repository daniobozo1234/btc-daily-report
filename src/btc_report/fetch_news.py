from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
from bs4 import BeautifulSoup

from btc_report.http_client import get_text

# Tematyka krypto — odrzuca ogólne newsy z portali mieszanych (Comparic, Money.pl itd.)
_CRYPTO_RE = re.compile(
    r"(bitcoin|\bbtc\b|ethereum|\beth\b|krypto|crypto|blockchain|binance|coinbase|"
    r"solana|stablecoin|\bdefi\b|\bnft\b|web3|altcoin|mempool|halving|satoshi|"
    r"kryptowalut\w*|tokeniz\w*|on[\s-]?chain|wieloryb|"
    r"\busdt\b|\busdc\b|tether|\bxrp\b|ripple|dogecoin|cardano|polygon|avalanche|"
    r"\bmica\b|miça|\bokx\b|bybit|kraken|metamask|\bledger\b|"
    r"spot\s+etf|bitcoin\s+etf|crypto\s+etf|blackrock.*bitcoin|bitcoin.*etf)",
    re.IGNORECASE,
)


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


def _parse_dt_string(raw_s: str) -> datetime | None:
    raw_s = (raw_s or "").strip()
    if not raw_s:
        return None
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


def _parse_date(entry: Any) -> str | None:
    """Data publikacji z RSS. Preferuj published; updated tylko gdy brak published.

    Google News często ustawia świeże published = data indeksacji, nie artykułu —
    takie linki odrzucamy osobno (_is_google_news_link).
    """
    for key in ("published", "updated"):
        raw = getattr(entry, key, None) or (entry.get(key) if isinstance(entry, dict) else None)
        if not raw:
            continue
        dt = _parse_dt_string(str(raw))
        if dt:
            return dt.isoformat()
    for key in ("published_parsed", "updated_parsed"):
        struct = getattr(entry, key, None)
        if not struct:
            continue
        try:
            dt = datetime(*struct[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            continue
    return None


def _is_google_news_link(link: str) -> bool:
    low = (link or "").lower()
    return "news.google." in low or "news.google.com" in low


_HTML_DATE_PATTERNS = (
    re.compile(
        r'"datePublished"\s*:\s*"([^"]+)"',
        re.IGNORECASE,
    ),
    re.compile(
        r'property=["\']article:published_time["\']\s+content=["\']([^"\']+)',
        re.IGNORECASE,
    ),
    re.compile(
        r'content=["\']([^"\']+)["\']\s+property=["\']article:published_time["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'itemprop=["\']datePublished["\']\s+content=["\']([^"\']+)',
        re.IGNORECASE,
    ),
    re.compile(
        r'content=["\']([^"\']+)["\']\s+itemprop=["\']datePublished["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+name=["\']pubdate["\'][^>]+content=["\']([^"\']+)',
        re.IGNORECASE,
    ),
)


def extract_html_published_at(html: str) -> str | None:
    """Wyciągnij datę publikacji z HTML (JSON-LD / OpenGraph)."""
    if not html:
        return None
    head = html[:120_000]
    for pat in _HTML_DATE_PATTERNS:
        m = pat.search(head)
        if not m:
            continue
        dt = _parse_dt_string(m.group(1))
        if dt:
            return dt.isoformat()
    return None


def _is_crypto_related(title: str, summary: str = "") -> bool:
    blob = f"{title} {summary}"
    return bool(_CRYPTO_RE.search(blob))


def _is_fresh(published_at: str | None, max_age_days: int | None) -> bool:
    if max_age_days is None or max_age_days <= 0:
        return True
    if not published_at:
        # bez daty nie wpuszczamy do daily — stąd brały się artykuły z 2024
        return False
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt >= datetime.now(timezone.utc) - timedelta(days=max_age_days)


def _entry_source_name(entry: Any, fallback: str) -> str:
    src = getattr(entry, "source", None)
    if src is not None:
        title = getattr(src, "title", None) or (src.get("title") if isinstance(src, dict) else None)
        if title:
            return str(title).strip()
    return fallback


def fetch_feed(
    name: str,
    url: str,
    lang: str,
    category: str,
    limit: int = 10,
    *,
    require_crypto: bool = False,
    max_age_days: int | None = None,
) -> list[Article]:
    try:
        body = get_text(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
            timeout=25.0,
        )
        parsed = feedparser.parse(body)
    except Exception:
        parsed = feedparser.parse(url)

    articles: list[Article] = []
    skipped_old = skipped_offtopic = 0
    for entry in parsed.entries[: max(limit * 3, 15)]:
        title = _clip(getattr(entry, "title", "") or "(bez tytułu)", 180)
        link = getattr(entry, "link", "") or ""
        raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
        if hasattr(entry, "content") and entry.content:
            try:
                raw_summary = entry.content[0].value or raw_summary
            except Exception:
                pass
        summary = _clip(raw_summary, 500)
        if not link:
            continue
        # Google News: data w RSS ≠ data artykułu (np. sierpień wraca jako „dziś”)
        if _is_google_news_link(link):
            skipped_old += 1
            continue
        published_at = _parse_date(entry)
        if not _is_fresh(published_at, max_age_days):
            skipped_old += 1
            continue
        if require_crypto and not _is_crypto_related(title, summary):
            skipped_offtopic += 1
            continue
        source = _entry_source_name(entry, name)
        articles.append(
            Article(
                title=title,
                link=link,
                summary=summary,
                source=source,
                lang=lang,
                published_at=published_at,
                category=category,
                title_pl=title if lang == "pl" else None,
                summary_pl=summary if lang == "pl" and len(summary) > 40 else None,
            )
        )
        if len(articles) >= limit:
            break
    if skipped_old or skipped_offtopic:
        print(
            f"  · {name}: wzięto {len(articles)}"
            f" (odrzucono stare={skipped_old}, off-topic={skipped_offtopic})"
        )
    return articles


def collect_articles(config: dict) -> dict[str, list[Article]]:
    limits = config.get("limits") or {}
    n_crypto = int(limits.get("crypto_articles", 14))
    n_macro = int(limits.get("macro_articles", 6))
    per_feed = int(limits.get("per_feed", 5))
    max_age = int(limits.get("max_article_age_days", 4))

    pl: list[Article] = []
    for src in config.get("rss_crypto_pl") or []:
        pl.extend(
            fetch_feed(
                src["name"],
                src["url"],
                src.get("lang", "pl"),
                "crypto_pl",
                limit=per_feed,
                require_crypto=True,
                max_age_days=max_age,
            )
        )
    en: list[Article] = []
    for src in config.get("rss_crypto_en") or []:
        en.extend(
            fetch_feed(
                src["name"],
                src["url"],
                src.get("lang", "en"),
                "crypto_en",
                limit=per_feed,
                require_crypto=True,
                max_age_days=max_age,
            )
        )
    macro: list[Article] = []
    for src in config.get("rss_macro") or []:
        macro.extend(
            fetch_feed(
                src["name"],
                src["url"],
                src.get("lang", "pl"),
                "macro",
                limit=4,
                require_crypto=False,
                max_age_days=max(max_age, 7),
            )
        )

    def sort_key(a: Article) -> str:
        return a.published_at or ""

    pl = _diversify(sorted(pl, key=sort_key, reverse=True), n_crypto)
    en = _diversify(sorted(en, key=sort_key, reverse=True), n_crypto)
    macro = _dedupe(sorted(macro, key=sort_key, reverse=True))[:n_macro]
    print(f"  artykuły po filtrach: PL={len(pl)}, EN={len(en)}, makro={len(macro)} (max {max_age}d)")
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
