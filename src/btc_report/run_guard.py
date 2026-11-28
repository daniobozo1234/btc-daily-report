"""Ochrona przed podwójnym mailem (GitHub schedule spóźniony + zewnętrzny cron)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from btc_report import config as cfg

WARSAW = ZoneInfo("Europe/Warsaw")


def _api_get(url: str, token: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "btc-daily-report-guard",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def already_sent_successfully_today(*, tz_name: str = "Europe/Warsaw") -> bool:
    """True, jeśli dziś (Warszawa) był już inny udany run tego workflow.

    Dzięki temu spóźniony `schedule` GitHuba nie wyśle drugiego maila,
    gdy rano odpalił punktualny zewnętrzny cron (workflow_dispatch).
    """
    token = (cfg.env("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    repo = (
        (cfg.env("GITHUB_REPO") or "").strip()
        or (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    )
    if not token or not repo:
        return False

    current_id = str(os.environ.get("GITHUB_RUN_ID") or "").strip()
    today = datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/daily-report.yml/runs"
        f"?per_page=15"
    )
    try:
        data = _api_get(url, token)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"! run_guard: nie sprawdzę wcześniejszych runów ({exc})")
        return False

    for run in data.get("workflow_runs") or []:
        rid = str(run.get("id") or "")
        if current_id and rid == current_id:
            continue
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            continue
        created = run.get("created_at") or ""
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(WARSAW)
        except ValueError:
            continue
        if dt.strftime("%Y-%m-%d") == today:
            print(
                f"→ run_guard: dziś był już udany run id={rid} "
                f"({dt.strftime('%H:%M')} {tz_name}, event={run.get('event')}) — pomijam wysyłkę"
            )
            return True
    return False
