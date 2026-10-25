from __future__ import annotations

import re

from bs4 import BeautifulSoup

from btc_report.http_client import get_text


def fetch_article_excerpt(url: str, max_chars: int = 1200) -> str:
    """Best-effort plain-text excerpt from article page for better AI summaries."""
    try:
        html = get_text(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; btc-daily-report/1.0)"},
            timeout=20.0,
        )
    except Exception:
        return ""

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()

    candidates = []
    for sel in ("article", "main", "[itemprop='articleBody']", ".article-content", ".post-content"):
        node = soup.select_one(sel)
        if node:
            candidates.append(node.get_text(" ", strip=True))
    if not candidates:
        candidates.append(soup.get_text(" ", strip=True))

    text = max(candidates, key=len)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 80:
        return ""
    return text[:max_chars]


def enrich_with_excerpts(articles: list[dict], limit: int = 5) -> list[dict]:
    out = []
    for i, a in enumerate(articles):
        item = dict(a)
        if i < limit and a.get("link"):
            excerpt = fetch_article_excerpt(a["link"])
            if excerpt:
                item["excerpt"] = excerpt
        out.append(item)
    return out
