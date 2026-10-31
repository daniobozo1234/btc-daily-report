from __future__ import annotations

import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from btc_report import config as cfg
from btc_report import store
from btc_report.analyze import analyze, build_brief, merge_enriched_articles
from btc_report.charts import render_etf_bars, render_whale_netflow
from btc_report.fetch_articles import enrich_with_excerpts
from btc_report.fetch_forum import fetch_bitcointalk
from btc_report.fetch_market import collect_market
from btc_report.fetch_news import collect_articles
from btc_report.fetch_whales import fetch_whale_flows
from btc_report.mailer import send_email, subject_line
from btc_report.periods import LABELS, detect_report_types, now_local, window_for
from btc_report.publish import publish_report_html
from btc_report.render import render_html


def build_report(report_types: list[str], *, send: bool, save_html: bool) -> int:
    """Pobiera dane raz, potem robi osobny raport (i mail) dla każdego typu."""
    config = cfg.load_config()
    cfg.ensure_dirs()
    tz = config.get("timezone") or "Europe/Warsaw"
    generated = now_local(tz)

    print("→ pobieram rynek…")
    market = collect_market().to_dict()
    store.save_market(market)

    print("→ pobieram ruchy wielorybów / netflow giełd…")
    whales = fetch_whale_flows().to_dict()

    print("→ pobieram RSS…")
    articles_obj = collect_articles(config)
    articles_raw = {k: [a.to_dict() for a in v] for k, v in articles_obj.items()}

    print("→ dociągam treść artykułów (excerpt)…")
    articles_raw["crypto_pl"] = enrich_with_excerpts(articles_raw["crypto_pl"], limit=10)
    articles_raw["crypto_en"] = enrich_with_excerpts(articles_raw["crypto_en"], limit=10)
    articles_raw["macro"] = enrich_with_excerpts(articles_raw["macro"], limit=4)
    print(f"  źródła PL: {len(articles_raw['crypto_pl'])}, EN: {len(articles_raw['crypto_en'])}")
    store.save_articles([*articles_raw["crypto_pl"], *articles_raw["crypto_en"], *articles_raw["macro"]])

    print("→ pobieram forum (+ fragmenty wątków)…")
    forum_cfg = config.get("forum") or {}
    forum = [
        t.to_dict()
        for t in fetch_bitcointalk(
            forum_cfg.get("bitcointalk_board_url", "https://bitcointalk.org/index.php?board=1.0"),
            forum_cfg.get("user_agent", "btc-daily-report/1.0"),
            limit=int((config.get("limits") or {}).get("forum_threads", 6)),
        )
    ]
    store.save_forum(forum)

    print("→ wykres ETF (dzień + tydzień)…")
    try:
        etf_chart_path = render_etf_bars(market.get("etf_flows") or [], market.get("etf_weekly") or [])
    except Exception as exc:
        print(f"! wykres ETF pominięty: {exc}")
        etf_chart_path = None

    print("→ wykres netflow wielorybów…")
    try:
        whale_chart_path = render_whale_netflow(
            whales.get("history_hourly") or [],
            whales.get("per_exchange_24h") or [],
        )
    except Exception as exc:
        print(f"! wykres whale pominięty: {exc}")
        whale_chart_path = None

    shared_news: dict = {}
    types = report_types or ["daily"]
    for idx, report_type in enumerate(types):
        print(f"→ raport {idx + 1}/{len(types)}: {LABELS.get(report_type, report_type)}…")
        win = window_for(report_type, generated, tz_name=tz)
        period_meta = {
            "type": report_type,
            "label_pl": win.label_pl,
            "start": win.start.isoformat(timespec="minutes"),
            "end": win.end.isoformat(timespec="minutes"),
            "start_pl": win.start.strftime("%d.%m.%Y"),
            "end_pl": win.end.strftime("%d.%m.%Y"),
        }
        period_history = store.period_market_summary(win.start, win.end)
        brief = build_brief(
            market,
            articles_raw,
            forum,
            [report_type],
            whales=whales,
            period=period_meta,
            period_history=period_history,
        )
        analysis = analyze(brief, shared_news=shared_news)
        articles = merge_enriched_articles(articles_raw, analysis)
        forum_block = analysis.get("forum") or {}

        generated_at = generated.isoformat(timespec="seconds")
        html = render_html(
            report_types=[report_type],
            market=market,
            articles=articles,
            forum_block=forum_block,
            analysis=analysis,
            tradingview_url=config.get("tradingview_url")
            or "https://www.tradingview.com/chart/?symbol=BITSTAMP%3ABTCUSD",
            tradingview_eth_url=config.get("tradingview_eth_url")
            or "https://www.tradingview.com/chart/?symbol=BITSTAMP%3AETHUSD",
            etf_chart_path=etf_chart_path,
            whale_chart_path=whale_chart_path,
            whales=whales,
            generated_at=generated_at,
            period=period_meta,
        )
        subject = subject_line([report_type], generated, market)
        day = generated.strftime("%Y%m%d")
        stamp = day if report_type == "daily" else f"{day}_{report_type}"
        report_filename = f"btc_eth_report_{stamp}.html"

        if save_html:
            out = cfg.OUTPUT_DIR / f"report_{generated.strftime('%Y%m%d_%H%M%S')}_{report_type}.html"
            out.write_text(html, encoding="utf-8")
            print(f"✓ zapisano HTML: {out}")
            if report_type == "daily":
                latest = cfg.OUTPUT_DIR / "report_latest.html"
                latest.write_text(html, encoding="utf-8")
                print(f"✓ najnowszy raport: {latest}")

        if send:
            print(f"→ publikuję {report_type} online…")
            report_url = publish_report_html(
                html,
                date_stamp=stamp,
                update_latest=(report_type == "daily"),
            )
            print(f"→ wysyłka Resend ({report_type})…")
            result = send_email(
                subject=subject,
                html=html,
                full_report_html=html,
                full_report_name=report_filename,
                report_types=[report_type],
                report_date=generated,
                report_url=report_url,
            )
            provider_id = result.get("id") if isinstance(result, dict) else None
            store.log_sent([report_type], subject, provider_id)
            print(f"✓ wysłano: {subject} (id={provider_id})")
        else:
            print(f"✓ raport gotowy (bez wysyłki): {subject}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BTC email report generator")
    parser.add_argument(
        "command",
        choices=["daily", "weekly", "monthly", "quarterly", "yearly", "auto"],
    )
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--no-html", action="store_true")
    args = parser.parse_args(argv)

    config = cfg.load_config()
    tz = config.get("timezone") or "Europe/Warsaw"
    report_types = detect_report_types(tz_name=tz) if args.command == "auto" else [args.command]

    print(f"Raporty: {', '.join(report_types)} @ {datetime.now(ZoneInfo(tz)).isoformat(timespec='seconds')}")
    try:
        return build_report(report_types, send=args.send, save_html=not args.no_html)
    except Exception as exc:
        print(f"BŁĄD: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
