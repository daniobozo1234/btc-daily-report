from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402

from btc_report.config import CHARTS_DIR, ensure_dirs
from btc_report.fetch_market import fetch_ohlc


def render_candles(
    days: int,
    outfile: Path | None = None,
    *,
    coin_id: str = "bitcoin",
    symbol: str = "BTC",
) -> Path:
    ensure_dirs()
    df = fetch_ohlc(days=days, coin_id=coin_id)
    if df.empty:
        raise RuntimeError(f"Brak danych OHLC do wykresu ({symbol})")

    plot_df = df.copy()
    plot_df.index = pd.to_datetime(plot_df.index).tz_localize(None)

    out = outfile or (CHARTS_DIR / f"{symbol.lower()}_candles_{days}d.png")
    mpf.plot(
        plot_df,
        type="candle",
        style="yahoo",
        title=f"{symbol}/USD — świece (~{days}d) — podgląd statyczny",
        ylabel="USD",
        volume=False,
        savefig=dict(fname=str(out), dpi=140, bbox_inches="tight"),
    )
    return out


def ohlc_for_plotly(days: int = 90, coin_id: str = "bitcoin") -> list[dict[str, Any]]:
    df = fetch_ohlc(days=days, coin_id=coin_id)
    if df.empty:
        return []
    rows = []
    for idx, row in df.iterrows():
        rows.append(
            {
                "time": idx.strftime("%Y-%m-%d"),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
            }
        )
    return rows


def render_whale_netflow(
    history: list[dict[str, Any]],
    per_exchange_24h: list[dict[str, Any]] | None = None,
    outfile: Path | None = None,
) -> Path | None:
    """Hourly netflow + optional 24h per-exchange bars."""
    if not history and not per_exchange_24h:
        return None
    ensure_dirs()

    has_ex = bool(per_exchange_24h)
    fig, axes = plt.subplots(2 if has_ex else 1, 1, figsize=(8.4, 6.2 if has_ex else 3.5), squeeze=False)

    ax0 = axes[0][0]
    if history:
        labels = [str(p.get("label", ""))[-8:] for p in history]
        values = [float(p.get("net_1h") or 0) for p in history]
        colors = ["#c62828" if v >= 0 else "#1a7f37" for v in values]
        ax0.bar(range(len(values)), values, color=colors, width=0.85)
        ax0.axhline(0, color="#333", linewidth=0.8)
        step = max(1, len(labels) // 8)
        ax0.set_xticks(range(0, len(labels), step))
        ax0.set_xticklabels([labels[i] for i in range(0, len(labels), step)], rotation=45, ha="right", fontsize=7)
        ax0.set_ylabel("BTC / 1h (net)")
        ax0.set_title("Netflow łącznie — czerwony: na giełdę, zielony: z giełdy")
        ax0.grid(axis="y", linestyle=":", alpha=0.4)
    else:
        ax0.text(0.5, 0.5, "Brak historii godzinowej", ha="center", va="center")
        ax0.set_axis_off()

    if has_ex:
        ax1 = axes[1][0]
        rows = [e for e in (per_exchange_24h or []) if e.get("has_data")]
        if not rows:
            rows = list(per_exchange_24h or [])
        names = [str(e.get("exchange")) for e in rows]
        nets = [float(e.get("net_btc") or 0) for e in rows]
        cols = ["#c62828" if v >= 0 else "#1a7f37" for v in nets]
        ax1.bar(names, nets, color=cols)
        ax1.axhline(0, color="#333", linewidth=0.8)
        ax1.set_ylabel("BTC / 24h (net)")
        ax1.set_title("Netflow 24h wg giełdy (tylko giełdy z danymi)")
        ax1.grid(axis="y", linestyle=":", alpha=0.4)
        for tick in ax1.get_xticklabels():
            tick.set_rotation(15)

    out = outfile or (CHARTS_DIR / "btc_whale_netflow.png")
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return out


def render_etf_bars(
    daily: list[dict[str, Any]],
    weekly: list[dict[str, Any]] | None = None,
    outfile: Path | None = None,
) -> Path | None:
    """Daily green/red bars + optional weekly panel."""
    if not daily:
        return None
    ensure_dirs()

    daily_rows = list(reversed(daily[:30]))
    d_labels = [str(r.get("date", ""))[-5:] for r in daily_rows]
    d_values = [float(r.get("flow_musd") or 0) for r in daily_rows]
    d_colors = ["#1a7f37" if v >= 0 else "#c62828" for v in d_values]

    has_weekly = bool(weekly)
    fig_h = 6.2 if has_weekly else 3.6
    rows = 2 if has_weekly else 1
    fig, axes = plt.subplots(rows, 1, figsize=(8.4, fig_h), squeeze=False)

    ax0 = axes[0][0]
    ax0.bar(range(len(d_values)), d_values, color=d_colors)
    ax0.axhline(0, color="#333", linewidth=0.8)
    ax0.set_xticks(range(len(d_labels)))
    ax0.set_xticklabels(d_labels, rotation=45, ha="right", fontsize=7)
    ax0.set_ylabel("mln USD")
    ax0.set_title("ETF BTC — napływy dzienne (zielone) / odpływy (czerwone)")
    ax0.grid(axis="y", linestyle=":", alpha=0.4)

    if has_weekly:
        w_rows = weekly[-12:]
        w_labels = [str(r.get("period", "")) for r in w_rows]
        w_values = [float(r.get("flow_musd") or 0) for r in w_rows]
        w_colors = ["#1a7f37" if v >= 0 else "#c62828" for v in w_values]
        ax1 = axes[1][0]
        ax1.bar(range(len(w_values)), w_values, color=w_colors)
        ax1.axhline(0, color="#333", linewidth=0.8)
        ax1.set_xticks(range(len(w_labels)))
        ax1.set_xticklabels(w_labels, rotation=45, ha="right", fontsize=8)
        ax1.set_ylabel("mln USD")
        ax1.set_title("ETF BTC — sumy tygodniowe")
        ax1.grid(axis="y", linestyle=":", alpha=0.4)

    out = outfile or (CHARTS_DIR / "btc_etf_flows.png")
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return out
