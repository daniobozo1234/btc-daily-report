from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from btc_report.config import DATA_DIR, ensure_dirs

CACHE_PATH = DATA_DIR / "last_run.json"


def save_last_run(payload: dict[str, Any]) -> Path:
    ensure_dirs()
    data = dict(payload)
    data["cached_at"] = datetime.now(timezone.utc).isoformat()
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    return CACHE_PATH


def load_last_run() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        raise FileNotFoundError(
            f"Brak cache ({CACHE_PATH}). Najpierw odpal pełne: python -m btc_report daily"
        )
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
