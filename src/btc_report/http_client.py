from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

DEFAULT_HEADERS = {
    "User-Agent": "btc-daily-report/1.0 (+personal research; contact via repo)",
    "Accept": "application/json, text/html, application/rss+xml, */*",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_json(url: str, *, params: dict | None = None, headers: dict | None = None, timeout: float = 30.0):
    h = {**DEFAULT_HEADERS, **(headers or {})}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=h) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        return r.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_text(url: str, *, headers: dict | None = None, timeout: float = 30.0) -> str:
    h = {**DEFAULT_HEADERS, **(headers or {})}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=h) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.text


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_bytes(url: str, *, headers: dict | None = None, timeout: float = 45.0) -> bytes:
    h = {**DEFAULT_HEADERS, **(headers or {})}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=h) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.content
