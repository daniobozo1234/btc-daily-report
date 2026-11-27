"""Kalendarz wydarzeń makro (stopy Fed, CPI, NFP, ECB…) — prognoza + wynik."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from btc_report.http_client import get_json

WARSAW = ZoneInfo("Europe/Warsaw")
HELIOS_URL = "https://api.helious.io/v1/calendar"

import re

# Tylko wydarzenia, które realnie ruszają risk-on / krypto (stopy, inflacja, praca, PKB, główne PMI)
_CRYPTO_RELEVANT = re.compile(
    r"("
    r"fed interest rate|fomc (press|economic|meeting|minutes)|interest rate decision|"
    r"interest rate projections|ecb interest|ecb press|ecb monetary policy statement|"
    r"consumer price index|core cpi|pce price|core pce|"
    r"harmonized index of consumer prices|core harmonized|"
    r"nonfarm payrolls|unemployment rate|"
    r"average hourly earnings|"
    r"adp employment|"
    r"initial jobless claims|"
    r"jolts job openings|"
    r"gdp growth|"
    r"ism manufacturing pmi$|ism services pmi$|"
    r"retail sales \(mom\)|retail sales \(yoy\)|"
    r"^retail sales$"
    r")",
    re.IGNORECASE,
)
_SKIP_NOISE = re.compile(
    r"("
    r"auction|earnings|holiday|thanksgiving|summit|speech|"
    r"meeting accounts|beige book|"
    r"employment index|new orders|prices paid|"
    r"challenger|factory orders|productivity|unit labor|"
    r"michigan consumer expectations|uom 1-year|uom 5-year|"
    r"labor force participation|u6 underemployment|"
    r"business climate|chicago pmi|"
    r"4-week average"
    r")",
    re.IGNORECASE,
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


def _crypto_priority(ev: EconEvent) -> int:
    """Niższy = ważniejsze dla krypto (sort rosnąco)."""
    t = f"{ev.title} {ev.title_pl}".lower()
    if re.search(r"interest rate decision|fed interest|ecb interest", t):
        return 0
    if re.search(r"fomc press|ecb press|fomc economic projections|interest rate projections", t):
        return 1
    if re.search(r"\bcpi\b|consumer price|pce price|harmonized index|hicp", t):
        return 2
    if re.search(r"nonfarm|nfp|unemployment|adp employment|hourly earnings", t):
        return 3
    if re.search(r"gdp growth", t):
        return 4
    if re.search(r"ism (manufacturing|services) pmi", t):
        return 5
    if re.search(r"jobless claims|jolts|retail sales", t):
        return 6
    return 8


def _inflation_family(title: str) -> str | None:
    t = title.lower()
    if "core" in t and ("cpi" in t or "consumer price" in t or "harmonized" in t or "pce" in t):
        if "pce" in t:
            return "core_pce"
        if "harmonized" in t or "hicp" in t:
            return "core_hicp"
        return "core_cpi"
    if "pce" in t and "price" in t:
        return "pce"
    if "harmonized" in t or "hicp" in t:
        return "hicp"
    if "cpi" in t or "consumer price" in t:
        return "cpi"
    return None


def _drop_redundant_inflation_mom(events: list[EconEvent]) -> list[EconEvent]:
    """Gdy jest YoY i MoM tej samej inflacji tego samego dnia — zostaw YoY."""
    yoy_keys: set[tuple[str, str, str]] = set()
    for e in events:
        fam = _inflation_family(e.title)
        if not fam or "(yoy)" not in e.title.lower() and "yoy" not in e.title.lower():
            # also titles like "CPI (YoY)"
            if fam and re.search(r"\(yoy\)|\byoy\b", e.title, re.I):
                yoy_keys.add((e.when_utc[:10], e.country, fam))
            continue
        yoy_keys.add((e.when_utc[:10], e.country, fam))

    out: list[EconEvent] = []
    for e in events:
        fam = _inflation_family(e.title)
        if fam and re.search(r"\(mom\)|\bmom\b", e.title, re.I):
            if (e.when_utc[:10], e.country, fam) in yoy_keys:
                continue
        out.append(e)
    return out


def _should_keep(raw: dict[str, Any], *, min_impact: str = "medium") -> bool:
    title = (raw.get("title") or "").strip()
    if not title:
        return False
    if _SKIP_NOISE.search(title) and not re.search(
        r"fomc press|ecb press|interest rate decision|fed interest", title, re.I
    ):
        return False
    if not _CRYPTO_RELEVANT.search(title):
        return False
    impact = (raw.get("impact") or "Low").strip().title()
    order = {"Low": 0, "Holiday": 0, "Medium": 1, "High": 2}
    need = {"low": 0, "medium": 1, "high": 2}.get(min_impact.lower(), 1)
    if order.get(impact, 0) < need:
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

    events = _drop_redundant_inflation_mom(events)

    past = sorted(
        [e for e in events if e.happened],
        key=lambda e: (_crypto_priority(e), e.when_utc),
        reverse=True,
    )
    upcoming = sorted(
        [e for e in events if not e.happened],
        key=lambda e: (_crypto_priority(e), e.when_utc),
    )

    upcoming_cap = max(10, limit - 4)
    past_cap = max(4, limit - upcoming_cap)
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
