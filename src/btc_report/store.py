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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
