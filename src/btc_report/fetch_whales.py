from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from btc_report import config as cfg
from btc_report.http_client import get_json

EXCHANGES = ("binance", "coinbase", "kraken", "okx", "bybit")


@dataclass
class WhaleSnapshot:
    fetched_at: str
    source: str
    net_1h_btc: float | None = None
    net_6h_btc: float | None = None
    net_24h_btc: float | None = None
    net_7d_btc: float | None = None
    inflow_24h_btc: float | None = None
    outflow_24h_btc: float | None = None
    bias_24h: str | None = None
    bias_7d: str | None = None
    note: str | None = None
    coverage_note: str | None = None
    per_exchange_24h: list[dict[str, Any]] = field(default_factory=list)
    missing_exchanges: list[str] = field(default_factory=list)
    history_hourly: list[dict[str, Any]] = field(default_factory=list)
    recent_spikes: list[dict[str, Any]] = field(default_factory=list)
    related_spikes: list[dict[str, Any]] = field(default_factory=list)
    whale_alert_txs: list[dict[str, Any]] = field(default_factory=list)
    large_onchain: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bias(net: float | None) -> str:
    if net is None:
        return "brak danych"
    if net > 50:
        return "napływ na giełdy (często presja sprzedaży)"
    if net < -50:
        return "odpływ z giełd (często akumulacja / cold storage)"
    return "bilans blisko zera (rynek wyczekujący)"


def fetch_whale_flows() -> WhaleSnapshot:
    """Netflow (CryptoDataAPI) + opcjonalnie Whale Alert + duże tx z mempool.space."""
    now = datetime.now(timezone.utc).isoformat()
    snap = _fetch_cryptodataapi(now)
    wa = _fetch_whale_alert()
    if wa:
        snap.whale_alert_txs = wa
        snap.source = f"{snap.source}+whalealert" if snap.source else "whalealert"
        # uzupełnij spike’y BTC etykietami z Whale Alert (lepsze niż same zera CDA)
        for tx in wa:
            if tx.get("symbol") != "BTC":
                continue
            snap.recent_spikes.append(
                {
                    "ts": tx.get("ts"),
                    "exchange": tx.get("exchange_hint") or tx.get("to_owner") or tx.get("from_owner") or "—",
                    "direction": tx.get("direction"),
                    "symbol": "BTC",
                    "amount": tx.get("amount"),
                    "amount_btc": tx.get("amount"),
                    "amount_usd": tx.get("amount_usd"),
                    "chain": "btc",
                    "source": "whalealert",
                    "from_owner": tx.get("from_owner"),
                    "to_owner": tx.get("to_owner"),
                    "hash": tx.get("hash"),
                }
            )
        # dedupe / limit
        snap.recent_spikes = _dedupe_spikes(snap.recent_spikes)[:12]

    onchain = _fetch_large_onchain_mempool()
    if onchain:
        snap.large_onchain = onchain

    sources = ["CryptoDataAPI (netflow)"]
    if wa:
        sources.append("Whale Alert (duże transfery z etykietami)")
    if onchain:
        sources.append("mempool.space (duże tx on-chain, bez etykiet giełd)")
    snap.coverage_note = (snap.coverage_note or "") + (
        f" Źródła tej rundy: {', '.join(sources)}."
    )
    return snap


def _fetch_cryptodataapi(now: str) -> WhaleSnapshot:
    key = cfg.env("CRYPTODATAAPI_KEY")
    if not key:
        return WhaleSnapshot(
            fetched_at=now,
            source="none",
            note="Brak CRYPTODATAAPI_KEY w .env — netflow giełdowy wyłączony (zostają inne źródła jeśli są).",
            coverage_note="Dodaj CRYPTODATAAPI_KEY, aby zobaczyć netflow per giełda.",
        )

    headers = {"X-API-Key": key, "User-Agent": "btc-daily-report/1.0"}
    # Każdy endpoint osobno — dawniej jeden błąd history (stary param limit)
    # kasował też żywy snapshot netflow.
    try:
        snap = get_json(
            "https://cryptodataapi.com/api/v1/on-chain/exchange-flows/BTC",
            headers=headers,
        )
    except Exception as exc:
        return WhaleSnapshot(
            fetched_at=now,
            source="cryptodataapi",
            note=f"Nie udało się pobrać whale/netflow: {exc}",
        )

    hist: dict[str, Any] = {}
    try:
        end = datetime.now(timezone.utc)
        start = end.timestamp() - 3 * 24 * 3600
        hist = get_json(
            "https://cryptodataapi.com/api/v1/on-chain/exchange-flows/BTC/history",
            params={
                "start": str(int(start)),
                "end": str(int(end.timestamp())),
                "interval": "1h",
            },
            headers=headers,
        ) or {}
    except Exception as exc:
        print(f"! CryptoDataAPI history pominięte: {exc}")

    spikes: dict[str, Any] = {}
    try:
        spikes = get_json(
            "https://cryptodataapi.com/api/v1/on-chain/exchange-flows/spike-alerts",
            headers=headers,
        ) or {}
    except Exception as exc:
        print(f"! CryptoDataAPI spike-alerts pominięte: {exc}")

    windows = (snap or {}).get("windows") or {}
    w1 = windows.get("1h") or {}
    w6 = windows.get("6h") or {}
    w24 = windows.get("24h") or {}
    w7 = windows.get("7d") or {}

    pe_raw = (snap or {}).get("per_exchange_24h") or {}
    per_exchange = []
    for ex in EXCHANGES:
        row = pe_raw.get(ex) or {}
        inflow = float(row.get("inflow") or 0)
        outflow = float(row.get("outflow") or 0)
        net = float(row.get("net") or 0)
        per_exchange.append(
            {
                "exchange": ex,
                "inflow_btc": inflow,
                "outflow_btc": outflow,
                "net_btc": net,
                "has_data": abs(net) > 0.01 or inflow > 0 or outflow > 0,
            }
        )
    for ex, row in pe_raw.items():
        if ex in EXCHANGES:
            continue
        inflow = float(row.get("inflow") or 0)
        outflow = float(row.get("outflow") or 0)
        net = float(row.get("net") or 0)
        per_exchange.append(
            {
                "exchange": ex,
                "inflow_btc": inflow,
                "outflow_btc": outflow,
                "net_btc": net,
                "has_data": abs(net) > 0.01 or inflow > 0 or outflow > 0,
            }
        )

    # w tabeli pokazujemy najpierw giełdy z danymi
    per_exchange.sort(key=lambda x: (not x["has_data"], x["exchange"]))
    missing = [x["exchange"] for x in per_exchange if not x["has_data"]]
    active = [x["exchange"] for x in per_exchange if x["has_data"]]

    points = []
    for p in (hist or {}).get("points") or []:
        ts = p.get("ts")
        try:
            tlabel = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%m-%d %H:%M")
        except Exception:
            tlabel = str(ts)
        pe1 = p.get("per_exchange_1h") or {}
        points.append(
            {
                "ts": ts,
                "label": tlabel,
                "net_1h": float(p.get("net_1h") or 0),
                "inflow_1h": float(p.get("inflow_1h") or 0),
                "outflow_1h": float(p.get("outflow_1h") or 0),
                "net_24h": float(p.get("net_24h") or 0) if p.get("net_24h") is not None else None,
                "per_exchange_1h": {
                    ex: {
                        "net": float((pe1.get(ex) or {}).get("net") or 0),
                        "inflow": float((pe1.get(ex) or {}).get("inflow") or 0),
                        "outflow": float((pe1.get(ex) or {}).get("outflow") or 0),
                    }
                    for ex in EXCHANGES
                    if ex in pe1
                },
            }
        )
    points = list(reversed(points[-48:]))

    btc_spikes = []
    related = []
    for a in (spikes or {}).get("alerts") or []:
        item = {
            "ts": a.get("ts"),
            "exchange": a.get("exchange"),
            "direction": a.get("direction"),
            "symbol": a.get("symbol"),
            "amount": float(a.get("amount") or 0),
            "amount_usd": float(a.get("amount_usd") or 0),
            "chain": a.get("chain"),
            "source": "cryptodataapi",
        }
        sym = str(a.get("symbol") or "").upper()
        if sym == "BTC":
            item["amount_btc"] = item["amount"]
            btc_spikes.append(item)
        elif str(a.get("exchange") or "").lower() in EXCHANGES and item["amount_usd"] >= 1_000_000:
            related.append(item)
    btc_spikes = btc_spikes[:10]
    related = related[:8]

    net24 = _f(w24.get("net"))
    net7 = _f(w7.get("net"))
    coverage_note = (
        "Rozbicie 24h (CryptoDataAPI free): niezerowy netflow BTC mają: "
        f"{', '.join(active) or 'brak'}. "
        + (
            f"Brak danych BTC dla: {', '.join(missing)} (limit free feed — to nie znaczy, że na tych giełdach nie było ruchu)."
            if missing
            else "Pokrycie free feed obejmuje wszystkie śledzone giełdy."
        )
    )
    note = (
        f"Netflow giełdowy BTC (CryptoDataAPI): 24h {net24:+.1f} BTC, 7d {net7:+.1f} BTC. "
        f"Plus = napływ NA giełdy; minus = odpływ Z giełd. "
        f"Bias 24h: {_bias(net24)}. Bias 7d: {_bias(net7)}."
    )

    return WhaleSnapshot(
        fetched_at=now,
        source="cryptodataapi",
        net_1h_btc=_f(w1.get("net")),
        net_6h_btc=_f(w6.get("net")),
        net_24h_btc=net24,
        net_7d_btc=net7,
        inflow_24h_btc=_f(w24.get("inflow")),
        outflow_24h_btc=_f(w24.get("outflow")),
        bias_24h=_bias(net24),
        bias_7d=_bias(net7),
        note=note,
        coverage_note=coverage_note,
        per_exchange_24h=per_exchange,
        missing_exchanges=missing,
        history_hourly=points,
        recent_spikes=btc_spikes,
        related_spikes=related,
    )


def _fetch_whale_alert() -> list[dict[str, Any]]:
    """Opcjonalnie: Whale Alert free/paid — duże transfery z etykietami ownerów."""
    key = cfg.env("WHALE_ALERT_API_KEY")
    if not key:
        return []
    start = int(time.time()) - 3500  # free tier ~1h historii
    try:
        data = get_json(
            "https://api.whale-alert.io/v1/transactions",
            params={
                "api_key": key,
                "min_value": "500000",
                "start": str(start),
                "limit": "50",
                "currency": "btc",
            },
            timeout=45.0,
        )
    except Exception as exc:
        print(f"! Whale Alert pominięte: {exc}")
        return []

    out: list[dict[str, Any]] = []
    for t in (data or {}).get("transactions") or []:
        symbol = str(t.get("symbol") or "").upper()
        if symbol not in {"BTC", "WBTC"} and str(t.get("blockchain") or "") not in {"bitcoin", "btc"}:
            # bierzemy też inne duże, ale oznaczamy
            pass
        amount = float(t.get("amount") or 0)
        amount_usd = float(t.get("amount_usd") or 0)
        from_o = ((t.get("from") or {}).get("owner") or "unknown").replace("_", " ")
        to_o = ((t.get("to") or {}).get("owner") or "unknown").replace("_", " ")
        from_t = (t.get("from") or {}).get("owner_type") or ""
        to_t = (t.get("to") or {}).get("owner_type") or ""
        direction = "transfer"
        exchange_hint = None
        if to_t == "exchange" and from_t != "exchange":
            direction = "inflow"
            exchange_hint = to_o
        elif from_t == "exchange" and to_t != "exchange":
            direction = "outflow"
            exchange_hint = from_o
        elif from_t == "exchange" and to_t == "exchange":
            direction = "exchange_to_exchange"
            exchange_hint = f"{from_o} → {to_o}"
        out.append(
            {
                "ts": t.get("timestamp"),
                "symbol": symbol or "BTC",
                "amount": amount,
                "amount_usd": amount_usd,
                "from_owner": from_o,
                "to_owner": to_o,
                "direction": direction,
                "exchange_hint": exchange_hint,
                "hash": t.get("hash"),
                "blockchain": t.get("blockchain"),
            }
        )
    return out[:20]


def _fetch_large_onchain_mempool(min_btc: float = 80.0, max_blocks: int = 3) -> list[dict[str, Any]]:
    """Duże tx z ostatnich bloków (mempool.space) — bez etykiet giełd, za to zawsze darmowe."""
    try:
        blocks = get_json("https://mempool.space/api/v1/blocks")[:max_blocks]
    except Exception as exc:
        print(f"! mempool blocks pominięte: {exc}")
        return []

    found: list[dict[str, Any]] = []
    for b in blocks:
        bid = b.get("id")
        height = b.get("height")
        if not bid:
            continue
        # duże transfery bywają głębiej w bloku — skanuj ~500 tx / blok
        for start in range(0, 500, 25):
            try:
                url = f"https://mempool.space/api/block/{bid}/txs" + (f"/{start}" if start else "")
                txs = get_json(url)
            except Exception:
                break
            if not txs:
                break
            for tx in txs:
                val = sum(int(o.get("value") or 0) for o in (tx.get("vout") or [])) / 1e8
                if val < min_btc:
                    continue
                txid = tx.get("txid") or ""
                found.append(
                    {
                        "block_height": height,
                        "amount_btc": round(val, 2),
                        "txid": txid,
                        "txid_short": txid[:16],
                        "url": f"https://mempool.space/tx/{txid}" if txid else None,
                        "fee_sats": tx.get("fee"),
                    }
                )
            if len(txs) < 25:
                break
    found.sort(key=lambda x: -float(x.get("amount_btc") or 0))
    uniq: list[dict[str, Any]] = []
    seen_txid = set()
    for x in found:
        if x["txid"] in seen_txid:
            continue
        seen_txid.add(x["txid"])
        uniq.append(x)
        if len(uniq) >= 10:
            break
    return uniq


def _dedupe_spikes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for s in items:
        key = (
            s.get("hash")
            or s.get("txid")
            or (s.get("exchange"), s.get("direction"), round(float(s.get("amount") or 0), 2), s.get("ts"))
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _f(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
