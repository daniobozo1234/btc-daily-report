from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ReportWindow:
    report_type: str
    start: datetime
    end: datetime
    label_pl: str


LABELS = {
    "daily": "Raport dzienny",
    "weekly": "Podsumowanie tygodnia",
    "monthly": "Podsumowanie miesiąca",
    "quarterly": "Podsumowanie kwartału",
    "yearly": "Podsumowanie roku",
}


def now_local(tz_name: str = "Europe/Warsaw") -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def window_for(report_type: str, when: datetime | None = None, tz_name: str = "Europe/Warsaw") -> ReportWindow:
    end = when or now_local(tz_name)
    if end.tzinfo is None:
        end = end.replace(tzinfo=ZoneInfo(tz_name))

    if report_type == "daily":
        start = end - timedelta(days=1)
    elif report_type == "weekly":
        # tydzień kalendarzowy: poniedziałek 00:00 → teraz
        start = (end - timedelta(days=end.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif report_type == "monthly":
        start = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif report_type == "quarterly":
        q_month = ((end.month - 1) // 3) * 3 + 1
        start = end.replace(month=q_month, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif report_type == "yearly":
        start = end.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        raise ValueError(f"Unknown report type: {report_type}")

    return ReportWindow(report_type, start, end, LABELS[report_type])


def is_last_day_of_month(d: date) -> bool:
    nxt = d + timedelta(days=1)
    return nxt.month != d.month


def is_quarter_end(d: date) -> bool:
    return d.month in (3, 6, 9, 12) and is_last_day_of_month(d)


def detect_report_types(when: datetime | None = None, tz_name: str = "Europe/Warsaw") -> list[str]:
    """Types due today. Daily always; weekly on Sunday; longer periods on calendar ends."""
    dt = when or now_local(tz_name)
    d = dt.date()
    types = ["daily"]
    if dt.weekday() == 6:  # Sunday
        types.append("weekly")
    if is_last_day_of_month(d):
        types.append("monthly")
    if is_quarter_end(d):
        types.append("quarterly")
    if d.month == 12 and d.day == 31:
        types.append("yearly")
    # Deduplicate preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in types:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def chart_days_for(report_type: str, chart_cfg: dict) -> int:
    key = f"{report_type}_days"
    return int(chart_cfg.get(key, chart_cfg.get("daily_days", 30)))
