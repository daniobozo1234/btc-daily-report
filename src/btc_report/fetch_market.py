from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup

from btc_report import config as cfg
from btc_report.http_client import get_json, get_text


@dataclass
class EtfFlowDay:
    date: str
    flow_musd: float


@dataclass
class MarketSnapshot:
    fetched_at: str
    btc_usd: float | None
    btc_eur: float | None
    btc_pln: float | None
    change_24h_pct: float | None
    change_7d_pct: float | None
    change_30d_pct: float | None
    volume_24h_usd: float | None
    market_cap_usd: float | None
    btc_dominance_pct: float | None
    eth_usd: float | None = None
    eth_eur: float | None = None
    eth_pln: float | None = None
    eth_change_24h_pct: float | None = None
    eth_change_7d_pct: float | None = None
    eth_change_30d_pct: float | None = None
    eth_volume_24h_usd: float | None = None
    eth_market_cap_usd: float | None = None
    eth_dominance_pct: float | None = None
    top_coins: list[dict[str, Any]] = field(default_factory=list)
    usd_pln: float | None = None
    eur_pln: float | None = None
    fear_greed: int | None = None
    fear_greed_label: str | None = None
    hashrate_eh_s: float | None = None
    etf_latest_date: str | None = None
    etf_latest_flow_musd: float | None = None
    etf_flow_note: str | None = None
    etf_flows: list[dict[str, Any]] = field(default_factory=list)
    etf_weekly: list[dict[str, Any]] = field(default_factory=list)
    etf_breakdown: list[dict[str, Any]] = field(default_factory=list)
    us10y_yield: float | None = None
    fed_funds: float | None = None
    halving_height_current: int | None = None
    halving_height_next: int | None = None
    halving_blocks_remaining: int | None = None
    halving_eta_days: float | None = None
    halving_eta_date: str | None = None
    halving_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cg_headers() -> dict[str, str]:
    key = cfg.env("COINGECKO_API_KEY")
    if key:
        return {"x-cg-demo-api-key": key}
    return {}


def fetch_coin_market(coin_id: str) -> dict[str, Any]:
    """CoinGecko market_data for one coin (bitcoin, ethereum, …)."""
    data = get_json(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}",
        params={
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "false",
        },
        headers=_cg_headers(),
    )
    md = data.get("market_data") or {}
    return {
        "usd": _f(md.get("current_price", {}).get("usd")),
        "eur": _f(md.get("current_price", {}).get("eur")),
        "pln": _f(md.get("current_price", {}).get("pln")),
        "change_24h_pct": _f(md.get("price_change_percentage_24h")),
        "change_7d_pct": _f(md.get("price_change_percentage_7d")),
        "change_30d_pct": _f(md.get("price_change_percentage_30d")),
        "volume_24h_usd": _f(md.get("total_volume", {}).get("usd")),
        "market_cap_usd": _f(md.get("market_cap", {}).get("usd")),
    }


def fetch_coingecko() -> dict[str, Any]:
    """BTC snapshot (kompatybilność wsteczna)."""
    b = fetch_coin_market("bitcoin")
    return {
        "btc_usd": b.get("usd"),
        "btc_eur": b.get("eur"),
        "btc_pln": b.get("pln"),
        "change_24h_pct": b.get("change_24h_pct"),
        "change_7d_pct": b.get("change_7d_pct"),
        "change_30d_pct": b.get("change_30d_pct"),
        "volume_24h_usd": b.get("volume_24h_usd"),
        "market_cap_usd": b.get("market_cap_usd"),
    }


def fetch_ethereum() -> dict[str, Any]:
    e = fetch_coin_market("ethereum")
    return {
        "eth_usd": e.get("usd"),
        "eth_eur": e.get("eur"),
        "eth_pln": e.get("pln"),
        "eth_change_24h_pct": e.get("change_24h_pct"),
        "eth_change_7d_pct": e.get("change_7d_pct"),
        "eth_change_30d_pct": e.get("change_30d_pct"),
        "eth_volume_24h_usd": e.get("volume_24h_usd"),
        "eth_market_cap_usd": e.get("market_cap_usd"),
    }


def fetch_dominance() -> tuple[float | None, float | None]:
    try:
        data = get_json("https://api.coingecko.com/api/v3/global", headers=_cg_headers())
        pct = (data.get("data") or {}).get("market_cap_percentage") or {}
        return _f(pct.get("btc")), _f(pct.get("eth"))
    except Exception:
        return None, None


def fetch_top_coins(limit: int = 10) -> list[dict[str, Any]]:
    """Top N coins by market cap — jak tabela CoinGecko (darmowe API)."""
    try:
        rows = get_json(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": str(limit),
                "page": "1",
                "sparkline": "false",
                "price_change_percentage": "24h,7d",
            },
            headers=_cg_headers(),
        )
    except Exception as exc:
        print(f"! top coins CoinGecko: {exc}")
        return []
    out: list[dict[str, Any]] = []
    for r in rows or []:
        out.append(
            {
                "rank": r.get("market_cap_rank"),
                "id": r.get("id"),
                "symbol": str(r.get("symbol") or "").upper(),
                "name": r.get("name"),
                "price_usd": _f(r.get("current_price")),
                "change_24h_pct": _f(r.get("price_change_percentage_24h")),
                "change_7d_pct": _f(
                    r.get("price_change_percentage_7d_in_currency")
                    if r.get("price_change_percentage_7d_in_currency") is not None
                    else r.get("price_change_percentage_7d")
                ),
                "market_cap_usd": _f(r.get("market_cap")),
                "volume_24h_usd": _f(r.get("total_volume")),
                "ath_usd": _f(r.get("ath")),
                "ath_change_pct": _f(r.get("ath_change_percentage")),
            }
        )
    return out


def fetch_nbp_rates() -> tuple[float | None, float | None]:
    usd = eur = None
    try:
        usd_data = get_json("https://api.nbp.pl/api/exchangerates/rates/a/usd/?format=json")
        usd = _f(usd_data["rates"][0]["mid"])
    except Exception:
        pass
    try:
        eur_data = get_json("https://api.nbp.pl/api/exchangerates/rates/a/eur/?format=json")
        eur = _f(eur_data["rates"][0]["mid"])
    except Exception:
        pass
    return usd, eur


def fetch_fear_greed() -> tuple[int | None, str | None]:
    try:
        data = get_json("https://api.alternative.me/fng/?limit=1")
        item = data["data"][0]
        return int(item["value"]), item.get("value_classification")
    except Exception:
        return None, None


def fetch_halving() -> dict[str, Any]:
    """Countdown to next Bitcoin subsidy halving (every 210_000 blocks)."""
    try:
        height = int(get_text("https://mempool.space/api/blocks/tip/height").strip())
    except Exception:
        return {
            "halving_height_current": None,
            "halving_height_next": None,
            "halving_blocks_remaining": None,
            "halving_eta_days": None,
            "halving_eta_date": None,
            "halving_note": "Nie udało się pobrać wysokości bloku.",
        }

    interval = 210_000
    next_h = ((height // interval) + 1) * interval
    remaining = max(0, next_h - height)
    # ~10 min / block
    from datetime import timedelta

    eta_days = remaining * 10 / (60 * 24)
    eta = datetime.now(timezone.utc) + timedelta(days=eta_days)
    note = (
        f"Następny halving przy bloku {next_h}. "
        f"Zostało {remaining} bloków (~{eta_days:.0f} dni, ok. {eta.date().isoformat()}). "
        f"Obecna wysokość: {height}."
    )
    return {
        "halving_height_current": height,
        "halving_height_next": next_h,
        "halving_blocks_remaining": remaining,
        "halving_eta_days": round(eta_days, 1),
        "halving_eta_date": eta.date().isoformat(),
        "halving_note": note,
    }


def fetch_hashrate_eh() -> float | None:
    try:
        data = get_json("https://mempool.space/api/v1/mining/hashrate/3d")
        hr = data.get("currentHashrate")
        if hr is None:
            return None
        return float(hr) / 1e18
    except Exception:
        return None


def fetch_etf_xoomar(days: int = 45) -> dict[str, Any]:
    """Free XOOMAR ETF flows (issuer holdings-derived). More up-to-date than Farside scrape.

    Covers major spot BTC ETFs that publish daily holdings (IBIT, BITB, ARKB, …).
    """
    data = get_json(
        "https://xoomar.com/api/markets/etf-flows",
        params={"asset": "btc", "days": str(days)},
        headers={"User-Agent": "Mozilla/5.0 (compatible; btc-daily-report/1.0)"},
    )
    rows = data.get("data") or []
    by_date: dict[str, float] = {}
    by_date_ticker: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        date_s = str(row.get("date") or "")
        ticker = str(row.get("ticker") or "?")
        flow = _f(row.get("flowUsd"))
        if not date_s or flow is None:
            continue
        by_date[date_s] = by_date.get(date_s, 0.0) + flow
        by_date_ticker.setdefault(date_s, []).append(
            {
                "ticker": ticker,
                "issuer": row.get("issuer"),
                "flow_usd": flow,
                "flow_musd": flow / 1_000_000.0,
            }
        )

    daily = [
        {"date": d, "flow_musd": by_date[d] / 1_000_000.0, "flow_usd": by_date[d]}
        for d in sorted(by_date.keys(), reverse=True)
    ]
    # weekly buckets (ISO week)
    weekly_map: dict[str, float] = {}
    for item in daily:
        d = datetime.strptime(item["date"], "%Y-%m-%d").date()
        iso = d.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        weekly_map[key] = weekly_map.get(key, 0.0) + item["flow_musd"]
    weekly = [{"period": k, "flow_musd": weekly_map[k]} for k in sorted(weekly_map.keys())]

    latest_date = daily[0]["date"] if daily else None
    latest_flow = daily[0]["flow_musd"] if daily else None
    breakdown = by_date_ticker.get(latest_date or "", [])
    note = (
        f"XOOMAR (darmowe API, dane z plików holdings emitentów): ostatni dzień {latest_date}, "
        f"suma IBIT+BITB+ARKB… ≈ {latest_flow:+.1f} mln USD. "
        f"Rozbicie dnia: "
        + ", ".join(f"{b['ticker']} {b['flow_musd']:+.1f}m" for b in breakdown[:6])
        + ". Źródło: https://xoomar.com/markets/api/etf-flows"
    )
    return {
        "source": "xoomar",
        "latest_date": latest_date,
        "latest_flow_musd": latest_flow,
        "note": note,
        "daily": daily,
        "weekly": weekly,
        "latest_breakdown": breakdown,
    }


def fetch_etf_flow_history_farside(limit: int = 20) -> list[EtfFlowDay]:
    """Fallback: parse Farside HTML table."""
    url = "https://farside.co.uk/btc/"
    html = get_text(url, headers={"User-Agent": "Mozilla/5.0 (compatible; btc-daily-report/1.0)"})
    soup = BeautifulSoup(html, "lxml")
    flows: list[EtfFlowDay] = []

    for table in soup.find_all("table"):
        for row in table.find_all("tr")[1:]:
            cols = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            if len(cols) < 2:
                continue
            date_s = cols[0]
            total_s = cols[-1]
            if not re.search(r"\d{4}|\d{1,2}\s+\w+|\d{1,2}[/-]\d{1,2}", date_s):
                continue
            if re.search(r"(?i)total|ytd|month|year|week", date_s) and not re.search(r"\d", date_s[:3]):
                continue
            total = _parse_flow_number(total_s)
            if total is None:
                continue
            flows.append(EtfFlowDay(date=date_s, flow_musd=total))
            if len(flows) >= limit:
                return flows
        if flows:
            break
    return flows


def fetch_etf_flows() -> tuple[str | None, float | None, str | None, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns date, latest_musd, note, daily_flows, weekly_flows, latest_breakdown."""
    try:
        x = fetch_etf_xoomar(days=45)
        if x.get("daily"):
            return (
                x["latest_date"],
                x["latest_flow_musd"],
                x["note"],
                x["daily"],
                x["weekly"],
                x.get("latest_breakdown") or [],
            )
    except Exception as exc:
        xoomar_err = str(exc)
    else:
        xoomar_err = "puste dane"

    try:
        history = fetch_etf_flow_history_farside(limit=20)
        if not history:
            return None, None, f"ETF: XOOMAR ({xoomar_err}) i Farside bez danych.", [], [], []
        latest = history[0]
        note = (
            f"Fallback Farside (może być opóźniony): {latest.date}, "
            f"≈ {latest.flow_musd:+.0f} mln USD. XOOMAR błąd: {xoomar_err}"
        )
        daily = [asdict(x) for x in history]
        return latest.date, latest.flow_musd, note, daily, [], []
    except Exception as exc:
        return None, None, f"ETF flows niedostępne: {exc}", [], [], []


def fetch_yahoo_last_close(symbol: str) -> float | None:
    """Free quote via Yahoo chart API (no key)."""
    try:
        data = get_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "1d", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        result = data["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]
        closes = [c for c in (quote.get("close") or []) if c is not None]
        if not closes:
            meta = result.get("meta") or {}
            return _f(meta.get("regularMarketPrice"))
        return float(closes[-1])
    except Exception:
        return None


def fetch_us10y() -> float | None:
    # FRED if key present, else Yahoo ^TNX (10Y yield proxy)
    fred = fetch_fred_series("DGS10")
    if fred is not None:
        return fred
    return fetch_yahoo_last_close("%5ETNX")


def fetch_fed_funds() -> float | None:
    fred = fetch_fred_series("DFEDTARU")
    if fred is not None:
        return fred
    # Fallback: EFFR proxy via Yahoo sometimes unavailable; leave None
    return None


def fetch_fred_series(series_id: str) -> float | None:
    key = cfg.env("FRED_API_KEY")
    if not key:
        return None
    try:
        data = get_json(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 5,
            },
        )
        for obs in data.get("observations", []):
            v = obs.get("value")
            if v and v != ".":
                return float(v)
    except Exception:
        return None
    return None


def fetch_ohlc(days: int = 30, coin_id: str = "bitcoin") -> pd.DataFrame:
    data = get_json(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc",
        params={"vs_currency": "usd", "days": str(min(max(days, 1), 365))},
        headers=_cg_headers(),
    )
    rows = []
    for ts, o, h, l, c in data:
        rows.append(
            {
                "Date": datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                "Open": o,
                "High": h,
                "Low": l,
                "Close": c,
                "Volume": 0.0,
            }
        )
    return pd.DataFrame(rows).set_index("Date")


def collect_market() -> MarketSnapshot:
    cg = fetch_coingecko()
    eth = fetch_ethereum()
    btc_dom, eth_dom = fetch_dominance()
    top = fetch_top_coins(10)
    usd_pln, eur_pln = fetch_nbp_rates()
    fg, fg_label = fetch_fear_greed()
    etf_date, etf_flow, etf_note, etf_flows, etf_weekly, etf_breakdown = fetch_etf_flows()
    halv = fetch_halving()
    return MarketSnapshot(
        fetched_at=datetime.now(timezone.utc).isoformat(),
        btc_usd=cg.get("btc_usd"),
        btc_eur=cg.get("btc_eur"),
        btc_pln=cg.get("btc_pln"),
        change_24h_pct=cg.get("change_24h_pct"),
        change_7d_pct=cg.get("change_7d_pct"),
        change_30d_pct=cg.get("change_30d_pct"),
        volume_24h_usd=cg.get("volume_24h_usd"),
        market_cap_usd=cg.get("market_cap_usd"),
        btc_dominance_pct=btc_dom,
        eth_usd=eth.get("eth_usd"),
        eth_eur=eth.get("eth_eur"),
        eth_pln=eth.get("eth_pln"),
        eth_change_24h_pct=eth.get("eth_change_24h_pct"),
        eth_change_7d_pct=eth.get("eth_change_7d_pct"),
        eth_change_30d_pct=eth.get("eth_change_30d_pct"),
        eth_volume_24h_usd=eth.get("eth_volume_24h_usd"),
        eth_market_cap_usd=eth.get("eth_market_cap_usd"),
        eth_dominance_pct=eth_dom,
        top_coins=top,
        usd_pln=usd_pln,
        eur_pln=eur_pln,
        fear_greed=fg,
        fear_greed_label=fg_label,
        hashrate_eh_s=fetch_hashrate_eh(),
        etf_latest_date=etf_date,
        etf_latest_flow_musd=etf_flow,
        etf_flow_note=etf_note,
        etf_flows=etf_flows,
        etf_weekly=etf_weekly,
        etf_breakdown=etf_breakdown,
        us10y_yield=fetch_us10y(),
        fed_funds=fetch_fed_funds(),
        **halv,
    )


def _f(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_flow_number(s: str) -> float | None:
    s = s.strip().replace(",", "")
    if not s or s in {"-", "–", "—"}:
        return 0.0
    # Farside often uses parentheses for negatives
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    val = float(m.group(0))
    return -abs(val) if neg else val
