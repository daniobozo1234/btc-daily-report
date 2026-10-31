from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from btc_report.periods import LABELS


def _env() -> Environment:
    templates = Path(__file__).parent / "templates"
    return Environment(
        loader=FileSystemLoader(str(templates)),
        autoescape=select_autoescape(["html", "xml"]),
    )


def fmt_num(v: Any, digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{digits}f}".replace(",", " ")
    except (TypeError, ValueError):
        return str(v)


def fmt_pct(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return str(v)


def _b64(path: Path | None) -> str | None:
    if path and path.exists():
        return base64.b64encode(path.read_bytes()).decode("ascii")
    return None


def render_html(
    *,
    report_types: list[str],
    market: dict[str, Any],
    articles: dict[str, list[dict]],
    forum_block: dict[str, Any],
    analysis: dict[str, Any],
    tradingview_url: str,
    tradingview_eth_url: str | None = None,
    etf_chart_path: Path | None = None,
    whale_chart_path: Path | None = None,
    whales: dict[str, Any] | None = None,
    generated_at: str,
    period: dict[str, Any] | None = None,
) -> str:
    titles = " + ".join(LABELS.get(t, t) for t in report_types)
    period = period or {}
    tmpl = _env().get_template("report.html")
    return tmpl.render(
        titles=titles,
        report_types=report_types,
        period=period,
        market=market,
        crypto_pl=articles.get("crypto_pl", []),
        crypto_en=articles.get("crypto_en", []),
        macro=articles.get("macro", []),
        forum_block=forum_block,
        analysis=analysis,
        tradingview_url=tradingview_url,
        tradingview_eth_url=tradingview_eth_url
        or "https://www.tradingview.com/chart/?symbol=BITSTAMP%3AETHUSD",
        etf_chart_b64=_b64(etf_chart_path),
        whale_chart_b64=_b64(whale_chart_path),
        whales=whales or {},
        generated_at=generated_at,
        fmt_num=fmt_num,
        fmt_pct=fmt_pct,
    )


def subject_for(report_types: list[str], market: dict[str, Any], generated_at: str) -> str:
    """Kompatybilność wsteczna — kanoniczny temat jest w mailer.subject_line."""
    from datetime import datetime

    from btc_report.mailer import subject_line

    try:
        dt = datetime.fromisoformat(generated_at)
    except ValueError:
        dt = datetime.now()
    return subject_line(report_types, dt, market)
