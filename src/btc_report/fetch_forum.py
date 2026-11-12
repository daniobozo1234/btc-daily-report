from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
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

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


@dataclass
class ForumThread:
    title: str
    link: str
    replies: str | None
    source: str
    excerpt: str | None = None
    last_post_at: str | None = None  # ISO UTC — ostatnia aktywność
    published_at: str | None = None  # ISO UTC — start wątku / publikacja posta

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


def _parse_btalk_datetime(raw: str, *, now: datetime | None = None) -> datetime | None:
    """Parsuje daty stylu SMF: 'November 03, 2026, 04:09:02 AM' / 'Today at 04:19:23 PM'."""
    if not raw:
        return None
    text = re.sub(r"\s+", " ", raw).strip()
    # odetnij prefiksy typu "Last post by X on ..."
    m = re.search(
        r"(?:^|\bon\s+)(Today|Yesterday)\s+at\s+(\d{1,2}:\d{2}:\d{2}\s*[AP]M)\b",
        text,
        re.I,
    )
    now = now or datetime.now(timezone.utc)
    if m:
        day_word, clock = m.group(1).lower(), m.group(2)
        try:
            t = datetime.strptime(clock.upper().replace(" ", ""), "%I:%M:%S%p").time()
        except ValueError:
            try:
                t = datetime.strptime(clock, "%I:%M:%S %p").time()
            except ValueError:
                return None
        base = now.astimezone(timezone.utc).date()
        if day_word == "yesterday":
            base = base - timedelta(days=1)
        return datetime.combine(base, t, tzinfo=timezone.utc)

    m = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(\d{1,2}),\s+(\d{4}),\s+(\d{1,2}:\d{2}:\d{2}\s*[AP]M)",
        text,
        re.I,
    )
    if not m:
        return None
    month = _MONTHS[m.group(1).lower()]
    day = int(m.group(2))
    year = int(m.group(3))
    clock = m.group(4)
    try:
        t = datetime.strptime(clock.upper().replace(" ", ""), "%I:%M:%S%p").time()
    except ValueError:
        try:
            t = datetime.strptime(clock, "%I:%M:%S %p").time()
        except ValueError:
            return None
    return datetime(year, month, day, t.hour, t.minute, t.second, tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _extract_dates_from_soup(soup: BeautifulSoup) -> list[datetime]:
    """Daty postów SMF są w div.smalltext — NIE w zegarze nagłówka (td.titlebg2)."""
    found: list[datetime] = []
    for el in soup.select("div.smalltext"):
        # pomiń nawigację / stopkę
        parent_classes = " ".join((el.parent.get("class") or []) if el.parent else []).lower()
        if "titlebg" in parent_classes:
            continue
        text = el.get_text(" ", strip=True)
        # czysta data posta albo „Today/Yesterday at …”
        if not re.search(
            r"(Today|Yesterday)\s+at\s+\d{1,2}:|"
            r"(January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+\d{1,2},\s+\d{4}",
            text,
            re.I,
        ):
            continue
        # odrzuć długie bloki (nie są samą datą)
        if len(text) > 80:
            continue
        dt = _parse_btalk_datetime(text)
        if dt:
            found.append(dt)
    return found


def fetch_thread_meta(url: str, user_agent: str) -> tuple[str, str | None, str | None]:
    """Zwraca (excerpt, last_post_at ISO, published_at ISO) z strony wątku."""
    try:
        html = get_text(url, headers={"User-Agent": user_agent}, timeout=20.0)
    except Exception:
        return "", None, None
    soup = BeautifulSoup(html, "lxml")

    # jeśli jest paginacja — spróbuj ostatniej strony (tam najnowsze posty)
    last_page_url = None
    page_nums: list[tuple[int, str]] = []
    for a in soup.select("a[href*='topic=']"):
        href = a.get("href") or ""
        m = re.search(r"topic=(\d+)\.(\d+)", href)
        label = a.get_text(" ", strip=True)
        if m and (label.isdigit() or label.lower() in {"last", "»", ">>"}):
            page_nums.append((int(m.group(2)), urljoin(url, href)))
    if page_nums:
        page_nums.sort(key=lambda x: x[0])
        cand = page_nums[-1][1]
        if cand.rstrip("/") != url.rstrip("/"):
            last_page_url = cand

    dates = _extract_dates_from_soup(soup)
    if last_page_url:
        try:
            html2 = get_text(last_page_url, headers={"User-Agent": user_agent}, timeout=20.0)
            soup2 = BeautifulSoup(html2, "lxml")
            dates.extend(_extract_dates_from_soup(soup2))
            # excerpt i tak z pierwszej strony / pierwszego posta
        except Exception:
            soup2 = None  # noqa: F841

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

    published = min(dates) if dates else None
    last_post = max(dates) if dates else None
    return _clean(text, 650), _iso(last_post), _iso(published)


def fetch_thread_excerpt(url: str, user_agent: str) -> str:
    excerpt, _, _ = fetch_thread_meta(url, user_agent)
    return excerpt


def _last_post_from_row(tr) -> datetime | None:
    if tr is None:
        return None
    # preferowane: span.smalltext w wierszu
    for sp in tr.select("span.smalltext"):
        dt = _parse_btalk_datetime(sp.get_text(" ", strip=True))
        if dt:
            return dt
    full = tr.get_text(" ", strip=True)
    m = re.search(
        r"Last post by .+? on ((?:Today|Yesterday) at \d{1,2}:\d{2}:\d{2}\s*[AP]M|"
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2},\s+\d{4},\s+\d{1,2}:\d{2}:\d{2}\s*[AP]M)",
        full,
        re.I,
    )
    if m:
        return _parse_btalk_datetime(m.group(1))
    return None


def fetch_bitcointalk(board_url: str, user_agent: str, limit: int = 8) -> list[ForumThread]:
    """Parse public BitcoinTalk board topic list + first-post excerpts + last activity."""
    ua = user_agent or DEFAULT_BROWSER_UA
    try:
        html = get_text(board_url, headers={"User-Agent": ua})
    except Exception as exc:
        print(f"  ! BitcoinTalk niedostępne: {exc}")
        return []

    soup = BeautifulSoup(html, "lxml")
    topic_anchors = soup.select("a[href*='topic=']")
    if len(topic_anchors) < 3:
        title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:80]
        print(
            f"  ! BitcoinTalk: podejrzanie mało linków topic= ({len(topic_anchors)}); "
            f"title={title!r} — prawdopodobnie blokada IP."
        )
        return []

    threads: list[ForumThread] = []
    selectors = "span.subject a[href*='topic='], td.subject a[href*='topic='], a[href*='topic=']"
    for a in soup.select(selectors):
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
        last_from_board = _last_post_from_row(a.find_parent("tr"))
        threads.append(
            ForumThread(
                title=title,
                link=link,
                replies=None,
                source="BitcoinTalk",
                last_post_at=_iso(last_from_board),
            )
        )
        if len(threads) >= limit:
            break

    for t in threads:
        excerpt, last_at, published_at = fetch_thread_meta(t.link, ua)
        t.excerpt = excerpt or None
        t.published_at = published_at or t.published_at
        # strona wątku ma zwykle dokładniejszą „ostatnią” datę
        if last_at:
            if not t.last_post_at or last_at > t.last_post_at:
                t.last_post_at = last_at

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
            counts = item.get("counts") or {}
            title = " ".join((post.get("name") or "").split())
            link = post.get("ap_id") or post.get("url") or ""
            if not title or not link or link in seen:
                continue
            body = post.get("body") or ""
            if post.get("deleted") or post.get("removed"):
                continue
            seen.add(link)
            published = post.get("published") or counts.get("published")
            last = counts.get("newest_comment_time") or published
            out.append(
                ForumThread(
                    title=title,
                    link=link,
                    replies=str(counts.get("comments") or "") or None,
                    source=f"Lemmy/{community}",
                    excerpt=_clean(body, 650) or None,
                    published_at=published,
                    last_post_at=last,
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
    seen = {t.link for t in lemmy}
    for t in threads:
        if t.link not in seen and len(lemmy) < limit:
            lemmy.append(t)
            seen.add(t.link)
    return lemmy[:limit]
