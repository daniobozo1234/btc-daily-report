from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from btc_report.config import DATA_DIR, ensure_dirs


def db_path() -> Path:
    ensure_dirs()
    return DATA_DIR / "report.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fetched_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fetched_at TEXT NOT NULL,
                link TEXT NOT NULL,
                title TEXT,
                source TEXT,
                category TEXT,
                lang TEXT,
                summary TEXT,
                published_at TEXT,
                UNIQUE(link)
            );
            CREATE TABLE IF NOT EXISTS forum_threads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fetched_at TEXT NOT NULL,
                link TEXT NOT NULL,
                title TEXT,
                source TEXT,
                UNIQUE(link, fetched_at)
            );
            CREATE TABLE IF NOT EXISTS sent_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sent_at TEXT NOT NULL,
                report_types TEXT NOT NULL,
                subject TEXT,
                provider_id TEXT
            );
            """
        )


def save_market(snapshot: dict[str, Any]) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO market_snapshots (fetched_at, payload) VALUES (?, ?)",
            (snapshot.get("fetched_at") or _now(), json.dumps(snapshot, ensure_ascii=False)),
        )


def save_articles(articles: list[dict[str, Any]]) -> None:
    init_db()
    now = _now()
    with connect() as conn:
        for a in articles:
            conn.execute(
                """
                INSERT INTO articles (fetched_at, link, title, source, category, lang, summary, published_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(link) DO UPDATE SET
                    fetched_at=excluded.fetched_at,
                    title=excluded.title,
                    summary=excluded.summary
                """,
                (
                    now,
                    a["link"],
                    a.get("title"),
                    a.get("source"),
                    a.get("category"),
                    a.get("lang"),
                    a.get("summary"),
                    a.get("published_at"),
                ),
            )


def save_forum(threads: list[dict[str, Any]]) -> None:
    init_db()
    now = _now()
    with connect() as conn:
        for t in threads:
            conn.execute(
                "INSERT OR IGNORE INTO forum_threads (fetched_at, link, title, source) VALUES (?, ?, ?, ?)",
                (now, t["link"], t.get("title"), t.get("source")),
            )


def log_sent(report_types: list[str], subject: str, provider_id: str | None) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO sent_reports (sent_at, report_types, subject, provider_id) VALUES (?, ?, ?, ?)",
            (_now(), ",".join(report_types), subject, provider_id),
        )


def articles_in_window(start: datetime, end: datetime, *, limit: int = 60) -> list[dict[str, Any]]:
    """Artykuły z bazy z published_at lub fetched_at w oknie okresu."""
    init_db()
    start_s = start.astimezone(timezone.utc).isoformat()
    end_s = end.astimezone(timezone.utc).isoformat()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT title, link, source, category, lang, summary, published_at, fetched_at
            FROM articles
            WHERE COALESCE(published_at, fetched_at) >= ?
              AND COALESCE(published_at, fetched_at) <= ?
            ORDER BY COALESCE(published_at, fetched_at) DESC
            LIMIT ?
            """,
            (start_s, end_s, limit),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for title, link, source, category, lang, summary, published_at, fetched_at in rows:
        out.append(
            {
                "title": title,
                "link": link,
                "source": source,
                "category": category,
                "lang": lang,
                "summary": (summary or "")[:400],
                "published_at": published_at or fetched_at,
                "is_wrap": False,
            }
        )
    return out


def period_market_summary(start: datetime, end: datetime) -> dict[str, Any]:
    """Min/max/start/end BTC(+ETH) z lokalnych snapshotów w oknie okresu."""
    init_db()
    start_s = start.astimezone(timezone.utc).isoformat()
    end_s = end.astimezone(timezone.utc).isoformat()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT fetched_at, payload FROM market_snapshots
            WHERE fetched_at >= ? AND fetched_at <= ?
            ORDER BY fetched_at ASC
            """,
            (start_s, end_s),
        ).fetchall()

    points: list[dict[str, Any]] = []
    for fetched_at, payload in rows:
        try:
            snap = json.loads(payload)
        except json.JSONDecodeError:
            continue
        btc = snap.get("btc_usd")
        if btc is None:
            continue
        points.append(
            {
                "fetched_at": fetched_at,
                "btc_usd": float(btc),
                "eth_usd": float(snap["eth_usd"]) if snap.get("eth_usd") is not None else None,
                "fear_greed": snap.get("fear_greed"),
                "etf_latest_flow_musd": snap.get("etf_latest_flow_musd"),
            }
        )

    if not points:
        return {"samples": 0, "note": "Brak lokalnych snapshotów w tym okresie — opieram się na zmianach % z CoinGecko."}

    btcs = [p["btc_usd"] for p in points]
    first, last = points[0], points[-1]
    chg = ((last["btc_usd"] / first["btc_usd"]) - 1.0) * 100.0 if first["btc_usd"] else None
    return {
        "samples": len(points),
        "start_at": first["fetched_at"],
        "end_at": last["fetched_at"],
        "btc_start": first["btc_usd"],
        "btc_end": last["btc_usd"],
        "btc_min": min(btcs),
        "btc_max": max(btcs),
        "btc_change_pct": chg,
        "eth_start": first.get("eth_usd"),
        "eth_end": last.get("eth_usd"),
        "fear_greed_last": last.get("fear_greed"),
        "note": f"Na podstawie {len(points)} lokalnych snapshotów z bazy raportu.",
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
