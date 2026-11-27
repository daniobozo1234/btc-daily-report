"""Kalendarz wydarzeń makro (stopy Fed, CPI, NFP, ECB…) — prognoza + wynik."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from btc_report.http_client import get_json

WARSAW = ZoneInfo("Europe/Warsaw")
HELIOS_URL = "https://api.helious.io/v1/calendar"

# Typy, które zostawiamy (bez earnings / wakacji / aukcji T-bill)
_KEEP_TYPES = {"economic", "fed_speech", "central_bank", "speech"}
_SKIP_TITLE_RE = (
    "auction",
    "earnings",
    "bill auction",
    "holiday",
    "bank holiday",
    "thanksgiving",
)

# Krótkie PL dla najczęstszych pozycji (reszta zostaje EN)
_TITLE_PL: dict[str, str] = {
    "Fed Interest Rate Decision": "Decyzja Fed o stopach procentowych",
    "FOMC Press Conference": "Konferencja prasowa FOMC",
    "FOMC Economic Projections": "Projekcje ekonomiczne FOMC",
    "FOMC Meeting Minutes": "Protokół posiedzenia FOMC",
    "FOMC Minutes": "Protokół FOMC",
    "Nonfarm Payrolls": "Zatrudnienie poza rolnictwem (NFP)",
    "Unemployment Rate": "Stopa bezrobocia (USA)",
    "Average Hourly Earnings (MoM)": "Płace godzinowe (MoM)",
    "Average Hourly Earnings (YoY)": "Płace godzinowe (YoY)",
    "Consumer Price Index (MoM)": "CPI inflacja (MoM)",
    "Consumer Price Index (YoY)": "CPI inflacja (YoY)",
    "Consumer Price Index ex Food & Energy (MoM)": "Core CPI (MoM)",
    "Consumer Price Index ex Food & Energy (YoY)": "Core CPI (YoY)",
    "Core PCE Price Index (MoM)": "Core PCE (MoM)",
    "Core PCE Price Index (YoY)": "Core PCE (YoY)",
    "PCE Price Index (MoM)": "PCE inflacja (MoM)",
    "PCE Price Index (YoY)": "PCE inflacja (YoY)",
    "GDP Growth Rate QoQ": "PKB (QoQ)",
    "GDP Growth Rate QoQ Adv": "PKB wstępny (QoQ)",
    "Initial Jobless Claims": "Pierwsze wnioski o zasiłek dla bezrobotnych",
    "ISM Manufacturing PMI": "ISM przemysł (PMI)",
    "ISM Services PMI": "ISM usługi (PMI)",
    "ADP Employment Change": "Zatrudnienie ADP",
    "JOLTS Job Openings": "Wolne miejsca pracy (JOLTS)",
    "Retail Sales (MoM)": "Sprzedaż detaliczna (MoM)",
    "Michigan Consumer Sentiment Index": "Nastroje konsumentów (UoM)",
    "ECB Interest Rate Decision": "Decyzja EBC o stopach",
    "ECB Press Conference": "Konferencja prasowa EBC",
    "ECB Monetary Policy Statement": "Komunikat polityki pieniężnej EBC",
}


@dataclass
class EconEvent:
    when_utc: str
    when_pl: str
    title: str
    title_pl: str
    country: str
    impact: str  # High / Medium / Low
    status: str  # due / printed / passed / delayed
    forecast: str | None
    previous: str | None
    actual: str | None
    surprise: str | None
    event_type: str
    happened: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s and s.lower() not in ("null", "none", "-") else None


def _title_pl(title: str) -> str:
    t = (title or "").strip()
    if t in _TITLE_PL:
        return _TITLE_PL[t]
    # częściowe dopasowanie (np. prefiks EU:)
    for en, pl in _TITLE_PL.items():
        if en.lower() in t.lower():
            return pl
    return t


def _parse_when(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _should_keep(raw: dict[str, Any], *, min_impact: str = "medium") -> bool:
    title = (raw.get("title") or "").strip()
    low = title.lower()
    if any(x in low for x in _SKIP_TITLE_RE):
        return False
    etype = (raw.get("type") or "economic").strip().lower()
    if etype not in _KEEP_TYPES and "rate" not in low and "fomc" not in low and "ecb" not in low:
        # pozwól na type=economic-like aliases
        if etype not in ("economic",):
            return False
    impact = (raw.get("impact") or "Low").strip().title()
    order = {"Low": 0, "Holiday": 0, "Medium": 1, "High": 2}
    need = {"low": 0, "medium": 1, "high": 2}.get(min_impact.lower(), 1)
    if order.get(impact, 0) < need:
        return False
    # High zawsze; Medium tylko economic / speech / central bank
    if impact == "Medium" and etype not in _KEEP_TYPES and "fomc" not in low and "ecb" not in low:
        return False
    return True


def _to_event(raw: dict[str, Any]) -> EconEvent | None:
    dt = _parse_when(raw.get("datetime_utc") or raw.get("datetime") or raw.get("date"))
    if not dt:
        return None
    status = (raw.get("status") or "due").strip().lower()
    actual = _norm(raw.get("actual"))
    happened = status in ("printed", "passed") or actual is not None
    # jeśli czas już minął UTC — traktuj jako „wydarzyło się” (nawet bez actual)
    if dt <= datetime.now(timezone.utc):
        happened = True
    title = (raw.get("title") or "").strip() or "(bez tytułu)"
    when_pl = dt.astimezone(WARSAW).strftime("%d.%m.%Y %H:%M")
    return EconEvent(
        when_utc=dt.isoformat(),
        when_pl=when_pl,
        title=title,
        title_pl=_title_pl(title),
        country=(raw.get("country") or "").strip().upper() or "—",
        impact=(raw.get("impact") or "Medium").strip().title(),
        status=status,
        forecast=_norm(raw.get("forecast")),
        previous=_norm(raw.get("previous")),
        actual=actual,
        surprise=_norm(raw.get("surprise")),
        event_type=(raw.get("type") or "economic").strip().lower(),
        happened=happened,
    )


def _fetch_country(country: str, *, days_back: int, days_ahead: int) -> list[dict[str, Any]]:
    try:
        data = get_json(
            HELIOS_URL,
            params={
                "days_back": str(max(0, days_back)),
                "days_ahead": str(max(1, days_ahead)),
                "country": country,
            },
            timeout=25.0,
        )
    except Exception as exc:
        print(f"  · kalendarz makro {country}: błąd {exc}")
        return []
    events = ((data or {}).get("data") or {}).get("events") or []
    if not isinstance(events, list):
        return []
    return [e for e in events if isinstance(e, dict)]


def fetch_econ_calendar(
    *,
    countries: list[str] | None = None,
    days_back: int = 3,
    days_ahead: int = 10,
    min_impact: str = "medium",
    limit: int = 28,
) -> dict[str, Any]:
    """Zwraca past + upcoming z prognozą / poprzednim / wynikiem."""
    countries = [c.strip().upper() for c in (countries or ["US", "EU"]) if c.strip()]
    raw_all: list[dict[str, Any]] = []
    for c in countries:
        raw_all.extend(_fetch_country(c, days_back=days_back, days_ahead=days_ahead))

    events: list[EconEvent] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in raw_all:
        if not _should_keep(raw, min_impact=min_impact):
            continue
        ev = _to_event(raw)
        if not ev:
            continue
        key = (ev.when_utc[:16], ev.country, ev.title)
        if key in seen:
            continue
        seen.add(key)
        events.append(ev)

    past = sorted([e for e in events if e.happened], key=lambda e: e.when_utc, reverse=True)
    upcoming = sorted(
        [e for e in events if not e.happened],
        key=lambda e: ({"High": 0, "Medium": 1, "Low": 2}.get(e.impact, 9), e.when_utc),
    )

    # więcej miejsca na nadchodzące (kalendarz „co przed nami”)
    upcoming_cap = max(12, (limit * 2) // 3)
    past_cap = max(6, limit - upcoming_cap)
    upcoming_out = [e.to_dict() for e in upcoming[:upcoming_cap]]
    past_out = [e.to_dict() for e in past[:past_cap]]

    print(
        f"  · kalendarz makro: nadchodzące={len(upcoming_out)}, "
        f"minione={len(past_out)} (kraje={','.join(countries)})"
    )
    return {
        "source": "Helious economic calendar",
        "countries": countries,
        "days_back": days_back,
        "days_ahead": days_ahead,
        "upcoming": upcoming_out,
        "past": past_out,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
