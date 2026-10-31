from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from btc_report import config as cfg


NEWS_SYSTEM = """Jesteś polskim redaktorem newsów o Bitcoinie/krypto.
Odpowiadasz WYŁĄCZNIE poprawnym JSON (bez markdown).
KAŻDE pole title_pl i summary_pl MUSI być w 100% po polsku.
summary_pl: dokładnie 3-5 pełnych zdań (każde z kropką na końcu). Opisz wątek: kto, co, dlaczego, znaczenie.
NIGDY nie urywaj zdania w połowie. Nie używaj HTML.

Schema zależnie od batcha — zwróć tylko klucze, które dostałeś na wejściu:
{
  "crypto_pl": [{"link":"...","title":"...","summary_pl":"..."}],
  "crypto_en": [{"link":"...","title_en":"...","title_pl":"...","summary_pl":"..."}],
  "macro": [{"link":"...","title":"...","title_pl":"...","summary_pl":"..."}]
}
"""

FORUM_SYSTEM = """Jesteś polskim analitykiem forów (BitcoinTalk).
Odpowiadasz WYŁĄCZNIE JSON. Cała treść po polsku — tytuły wątków też przetłumacz w polu title_pl.
Dla KAŻDEGO wątku summary_pl = dokładnie 3 kompletne zdania po polsku, każde zakończone kropką.
Zakaz urwanych słów typu „niebezp”, „zgłaszania” bez dokończenia. Lepiej krócej, ale domknięte.
overall: 4-5 zdań o klimacie dyskusji.
hot_topics: krótkie etykiety PO POLSKU.

Schema:
{
  "forum": {
    "overall": "...",
    "hot_topics": ["...", "...", "..."],
    "cold_or_fading": ["..."],
    "summaries": [
      {"link":"...","title":"oryginalny tytuł","title_pl":"polski tytuł","summary_pl":"Zdanie1. Zdanie2. Zdanie3."}
    ]
  }
}
Nie wymyślaj wątków spoza listy. Zwróć summaries dla wszystkich podanych wątków.
"""

WNIOSKI_SYSTEM = """Jesteś doświadczonym polskim analitykiem rynku Bitcoina i makroekonomii.
Odpowiadasz WYŁĄCZNIE JSON, całość po polsku.
Na podstawie wskaźników, newsów krypto PL/EN, makro i forów napisz ROZBUDOWANE podsumowanie rynku.

Schema:
{
  "tytul_dnia": "krótki, mocny tytuł dnia (po polsku)",
  "glowne_watki": [
    {"watek": "nazwa wątku", "opis": "2-3 zdania dlaczego to ważne dziś"}
  ],
  "trendy_rynku": [
    {"trend": "nazwa trendu", "kierunek": "wzrostowy|spadkowy|boczny|mieszany", "opis": "2-3 zdania"}
  ],
  "makro_a_btc": "akapit 4-6 zdań: jak stopy, obligacje, USD/PLN i newsy makro mogą wpływać na BTC teraz",
  "wieloryby": "akapit 3-5 zdań: interpretacja netflow giełdowego (napływ=presja sprzedaży, odpływ=akumulacja) + ewentualne spike'i",
  "obraz_rynku": "akapit 5-7 zdań: synteza ceny, Fear&Greed, ETF, hashrate, dominacji, halvingu, wielorybów i newsów",
  "scenariusze_btc": {
    "bazowy": "3-5 zdań — najbardziej prawdopodobne zachowanie BTC w najbliższych dniach/tygodniach",
    "optymistyczny": "2-4 zdania — co musiałoby się stać, by było mocniej w górę",
    "pesymistyczny": "2-4 zdania — co mogłoby pchnąć cenę w dół"
  },
  "na_radarze": ["konkret 1", "konkret 2", "konkret 3", "konkret 4"],
  "ryzyka": ["ryzyko 1", "ryzyko 2", "ryzyko 3"],
  "wnioski": ["krótki point 1", "point 2", "point 3", "point 4", "point 5"]
}

Zasady:
- Predykcje to SCENARIUSZE oparte na briefie, nie pewniki i nie rekomendacja kupna/sprzedaży.
- Nie wymyślaj newsów spoza briefu.
- Bądź konkretny (liczby z briefu, nazwy tematów).
- Uwzględnij pole whales (netflow): plus na giełdę często = większa podaż, minus = odpływ do cold wallets.
"""


def _period_focus_instruction(report_type: str, period: dict[str, Any] | None = None) -> str:
    """Instrukcja skali analizy — daily ≠ monthly."""
    period = period or {}
    label = period.get("label_pl") or report_type
    start = period.get("start") or ""
    end = period.get("end") or ""
    window = f" Okres: {start} → {end}." if start and end else ""
    if report_type == "daily":
        return (
            "To RAPORT DZIENNY: skup się na ostatnich ~24h i tym, co ważne NA DZIŚ. "
            "Nie rób retrospektywy całego miesiąca."
        )
    if report_type == "weekly":
        return (
            f"To PODSUMOWANIE TYGODNIA ({label}).{window} "
            "Analizuj CAŁY TYDZIEŃ: jak cena/zmiany, ETF, netflow i newsy ułożyły się przez te dni. "
            "Nie ograniczaj się do samego dzisiejszego dnia."
        )
    if report_type == "monthly":
        return (
            f"To PODSUMOWANIE MIESIĄCA ({label}).{window} "
            "Analizuj CAŁY MIESIĄC: trajektoria ceny (start/min/max/koniec z period_history), "
            "główne wątki miesiąca, bias netflow i makro w skali miesiąca. "
            "To NIE jest raport dzienny z inną etykietą."
        )
    if report_type == "quarterly":
        return (
            f"To PODSUMOWANIE KWARTAŁU ({label}).{window} "
            "Patrz na cały kwartał: trendy, reżimy rynku, kluczowe eventy. Nie pisz jak codziennego briefu."
        )
    if report_type == "yearly":
        return (
            f"To PODSUMOWANIE ROKU ({label}).{window} "
            "Perspektywa roczna: wielkie wątki, zmiany struktury rynku, wnioski na kolejny rok."
        )
    return f"Skala raportu: {label}.{window}"


def build_brief(
    market: dict[str, Any],
    articles: dict[str, list[dict]],
    forum: list[dict],
    report_types: list[str],
    whales: dict[str, Any] | None = None,
    period: dict[str, Any] | None = None,
    period_history: dict[str, Any] | None = None,
    period_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    whales = whales or {}
    return {
        "report_types": report_types,
        "period": period or {},
        "period_history": period_history or {},
        "period_context": period_context or {},
        "market": {
            k: market.get(k)
            for k in (
                "btc_usd",
                "btc_eur",
                "btc_pln",
                "change_24h_pct",
                "change_7d_pct",
                "change_30d_pct",
                "eth_usd",
                "eth_eur",
                "eth_pln",
                "eth_change_24h_pct",
                "eth_change_7d_pct",
                "eth_change_30d_pct",
                "eth_dominance_pct",
                "usd_pln",
                "eur_pln",
                "fear_greed",
                "fear_greed_label",
                "hashrate_eh_s",
                "btc_dominance_pct",
                "etf_latest_date",
                "etf_latest_flow_musd",
                "etf_flow_note",
                "us10y_yield",
                "fed_funds",
                "halving_blocks_remaining",
                "halving_eta_days",
                "halving_eta_date",
                "halving_note",
            )
        },
        "top_coins": [
            {
                "rank": c.get("rank"),
                "symbol": c.get("symbol"),
                "name": c.get("name"),
                "price_usd": c.get("price_usd"),
                "change_24h_pct": c.get("change_24h_pct"),
                "market_cap_usd": c.get("market_cap_usd"),
            }
            for c in (market.get("top_coins") or [])[:10]
        ],
        "whales": {
            "net_1h_btc": whales.get("net_1h_btc"),
            "net_24h_btc": whales.get("net_24h_btc"),
            "net_7d_btc": whales.get("net_7d_btc"),
            "inflow_24h_btc": whales.get("inflow_24h_btc"),
            "outflow_24h_btc": whales.get("outflow_24h_btc"),
            "bias_24h": whales.get("bias_24h"),
            "bias_7d": whales.get("bias_7d"),
            "note": whales.get("note"),
            "coverage_note": whales.get("coverage_note"),
            "per_exchange_24h": whales.get("per_exchange_24h") or [],
            "missing_exchanges": whales.get("missing_exchanges") or [],
            "recent_spikes": (whales.get("recent_spikes") or [])[:5],
            "related_spikes": (whales.get("related_spikes") or [])[:5],
            "whale_alert_txs": (whales.get("whale_alert_txs") or [])[:5],
            "large_onchain": (whales.get("large_onchain") or [])[:5],
        },
        "crypto_pl": _slim_articles(articles.get("crypto_pl", []), n=14, summary_chars=140),
        "crypto_en": _slim_articles(articles.get("crypto_en", []), n=14, summary_chars=140),
        "macro": _slim_articles(articles.get("macro", []), n=4, summary_chars=120),
        "forum": [
            {
                "title": t.get("title"),
                "link": t.get("link"),
                "excerpt": (t.get("excerpt") or "")[:280],
            }
            for t in forum[:5]
        ],
    }


def analyze(brief: dict[str, Any], *, shared_news: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _resolve_ai():
        return _fallback(brief)

    news_model = cfg.env("AI_MODEL_NEWS") or "qwen/qwen3.8-27b"
    base_model = cfg.env("AI_MODEL") or "openai/gpt-oss-20b"

    # Batches — mniej tokenów naraz, lepsze tłumaczenia
    pl_all = brief.get("crypto_pl") or []
    en_all = brief.get("crypto_en") or []

    if shared_news and shared_news.get("ready"):
        print("  · AI news/fora — używam cache z poprzedniego raportu w tej rundzie…")
        news_pl = shared_news["news_pl"]
        news_en = shared_news["news_en"]
        news_macro = shared_news["news_macro"]
        forum = shared_news["forum"]
    else:
        # Groq free: niski limit output tokens/min — małe batche + max_tokens<=900
        print("  · AI news PL (1/2)…")
        news_pl_a = _chat_json(
            NEWS_SYSTEM,
            "Streszcz PL newsy (3 pełne zdania, po polsku):\n"
            + json.dumps({"crypto_pl": pl_all[:7]}, ensure_ascii=False),
            model_override=news_model,
            max_tokens=900,
        ) or {}
        time.sleep(20)
        print("  · AI news PL (2/2)…")
        news_pl_b = _chat_json(
            NEWS_SYSTEM,
            "Streszcz PL newsy (3 pełne zdania, po polsku):\n"
            + json.dumps({"crypto_pl": pl_all[7:]}, ensure_ascii=False),
            model_override=news_model,
            max_tokens=900,
        ) or {}
        time.sleep(20)

        # EN: batche po 2 + slim input — mniej ucięć JSON / lepsze PL
        en_parts: list[dict] = []
        en_batch = 2
        en_total_batches = max(1, (len(en_all) + en_batch - 1) // en_batch) if en_all else 0
        for i in range(0, len(en_all), en_batch):
            chunk = _slim_en_for_translate(en_all[i : i + en_batch])
            print(f"  · AI news EN→PL ({i // en_batch + 1}/{en_total_batches})…")
            part = _chat_json(
                NEWS_SYSTEM,
                "Przetłumacz KAŻDY artykuł na polski. title_pl i summary_pl TYLKO po polsku. "
                "summary_pl = dokładnie 3 krótkie pełne zdania z kropkami. "
                "Zwróć crypto_en z WSZYSTKIMI linkami z wejścia. Krótki JSON, bez markdown.\n"
                + json.dumps({"crypto_en": chunk}, ensure_ascii=False),
                model_override=news_model,
                max_tokens=700,
            ) or {}
            got = part.get("crypto_en") or []
            missing_chunk = _missing_en_translations(chunk, got)
            if missing_chunk:
                time.sleep(22)
                for one in missing_chunk:
                    got = _merge_en_by_link(got, [_translate_one_en(one, news_model, base_model)])
                    time.sleep(16)
            en_parts.extend(got)
            if i + en_batch < len(en_all):
                time.sleep(22)

        print("  · AI makro…")
        news_macro = _chat_json(
            NEWS_SYSTEM,
            "Streszcz makro po polsku (3 pełne zdania):\n"
            + json.dumps({"macro": brief.get("macro") or []}, ensure_ascii=False),
            model_override=news_model,
            max_tokens=700,
        ) or {}
        time.sleep(22)

        print("  · AI fora (po polsku)…")
        forum_ai = _chat_json(
            FORUM_SYSTEM,
            "Przeanalizuj i przetłumacz wątki forum na polski:\n"
            + json.dumps({"forum_threads": brief.get("forum") or []}, ensure_ascii=False),
            model_override=news_model,
            max_tokens=900,
        ) or {}
        time.sleep(22)

        news_pl = {
            "crypto_pl": (news_pl_a.get("crypto_pl") or []) + (news_pl_b.get("crypto_pl") or [])
        }
        news_en = {"crypto_en": en_parts}

        missing_en = _missing_en_translations(en_all, news_en.get("crypto_en") or [])
        if missing_en:
            print(f"  · AI EN→PL dopychanie ({len(missing_en)} szt.)…")
            fixed = []
            for one in _slim_en_for_translate(missing_en):
                time.sleep(20)
                fixed.append(_translate_one_en(one, news_model, base_model))
            news_en["crypto_en"] = _merge_en_by_link(news_en.get("crypto_en") or [], fixed)
            still = _missing_en_translations(en_all, news_en.get("crypto_en") or [])
            if still:
                print(f"  · AI EN→PL ostatnia szansa ({len(still)} szt., model {base_model})…")
                more = []
                for one in _slim_en_for_translate(still):
                    time.sleep(25)
                    more.append(_translate_one_en(one, base_model, base_model))
                news_en["crypto_en"] = _merge_en_by_link(news_en.get("crypto_en") or [], more)

        forum = (forum_ai.get("forum") or {}) if isinstance(forum_ai, dict) else {}
        if not forum.get("summaries"):
            forum = _forum_fallback_pl(brief.get("forum") or [])

        for s in forum.get("summaries") or []:
            if not s.get("title_pl"):
                s["title_pl"] = s.get("title")
            s["summary_pl"] = _finish_sentence(s.get("summary_pl") or "")

        if shared_news is not None:
            shared_news.clear()
            shared_news.update(
                {
                    "ready": True,
                    "news_pl": news_pl,
                    "news_en": news_en,
                    "news_macro": news_macro,
                    "forum": forum,
                }
            )

    period = brief.get("period") or {}
    primary = (brief.get("report_types") or ["daily"])[0]
    period_focus = _period_focus_instruction(primary, period)

    # Rozbudowane podsumowanie = 3 wywołania + retry brakujących pól
    ctx = {
        "report_type": primary,
        "period": period,
        "period_history": brief.get("period_history") or {},
        "market": brief.get("market"),
        "whales": {
            k: (brief.get("whales") or {}).get(k)
            for k in (
                "net_24h_btc",
                "net_7d_btc",
                "bias_24h",
                "bias_7d",
                "note",
                "per_exchange_24h",
                "missing_exchanges",
                "recent_spikes",
                "whale_alert_txs",
                "large_onchain",
            )
        },
        "newsy_pl": [
            {"title": a.get("title"), "summary_pl": (a.get("summary_pl") or "")[:220]}
            for a in (news_pl.get("crypto_pl") or [])[:6]
        ],
        "newsy_en_pl": [
            {"title_pl": a.get("title_pl"), "summary_pl": (a.get("summary_pl") or "")[:220]}
            for a in (news_en.get("crypto_en") or [])[:6]
        ],
        "makro": [
            {
                "title_pl": a.get("title_pl") or a.get("title"),
                "summary_pl": (a.get("summary_pl") or "")[:180],
            }
            for a in (news_macro.get("macro") or [])[:3]
        ],
        "forum": {
            "overall": ((forum.get("overall") or "")[:280]),
            "hot_topics": (forum.get("hot_topics") or [])[:4],
        },
    }
    ctx_json = json.dumps(ctx, ensure_ascii=False)

    def _outlook_part(label: str, system: str, user: str) -> dict:
        print(f"  · {label}…")
        time.sleep(22)
        # najpierw bez response_format — Groq często ucina JSON przy force_json + długich akapitach
        part = _chat_json(system, user, model_override=news_model, max_tokens=700, force_json=False) or {}
        if not part:
            time.sleep(18)
            part = _chat_json(system, user, model_override=base_model, max_tokens=700, force_json=False) or {}
        return part

    part1 = _outlook_part(
        f"AI outlook 1/3 — wątki i trendy ({primary})",
        "Jesteś polskim analitykiem BTC. TYLKO JSON, po polsku. "
        + period_focus
        + " Każdy opis 2-3 krótkie zdania. Max 5 wątków i 4 trendy. Schema: "
        '{"tytul_dnia":"...","glowne_watki":[{"watek":"...","opis":"..."}],'
        '"trendy_rynku":[{"trend":"...","kierunek":"wzrostowy|spadkowy|boczny|mieszany","opis":"..."}]}',
        "Na podstawie briefu wypisz tytuł okresu, główne wątki i trendy:\n" + ctx_json,
    )
    part2 = _outlook_part(
        f"AI outlook 2/3 — makro, wieloryby, obraz rynku ({primary})",
        "Jesteś polskim analitykiem BTC. TYLKO JSON, po polsku. "
        + period_focus
        + " Każde pole = 3-4 krótkie zdania (nie więcej). Schema: "
        '{"makro_a_btc":"...","wieloryby":"...","obraz_rynku":"..."}',
        "Opisz makro vs BTC, netflow/wieloryby i obraz rynku w skali tego okresu:\n" + ctx_json,
    )
    part3 = _outlook_part(
        f"AI outlook 3/3 — scenariusze i wnioski ({primary})",
        "Jesteś polskim analitykiem BTC. TYLKO JSON, po polsku. "
        + period_focus
        + " Scenariusze po 2-3 zdania. Schema: "
        '{"scenariusze_btc":{"bazowy":"...","optymistyczny":"...","pesymistyczny":"..."},'
        '"na_radarze":["..."],"ryzyka":["..."],"wnioski":["..."]}',
        "Napisz scenariusze BTC, na_radarze (4), ryzyka (3), wnioski (5) dla tego okresu:\n" + ctx_json,
    )

    conclusions = {**part1, **part2, **part3}
    if part3.get("scenariusze_btc"):
        conclusions["scenariusze_btc"] = part3["scenariusze_btc"]

    # dopychanie pojedynczych brakujących sekcji (częsty efekt rate-limitu)
    missing_fields = []
    if not conclusions.get("glowne_watki"):
        missing_fields.append("glowne_watki")
    if not conclusions.get("obraz_rynku"):
        missing_fields.append("obraz_rynku")
    if not conclusions.get("wieloryby"):
        missing_fields.append("wieloryby")
    if not (conclusions.get("scenariusze_btc") or {}).get("bazowy"):
        missing_fields.append("scenariusze_btc")
    if not conclusions.get("wnioski"):
        missing_fields.append("wnioski")
    if missing_fields:
        print(f"  · AI outlook dopychanie pól: {', '.join(missing_fields)}…")
        time.sleep(25)
        patch = _chat_json(
            "Jesteś polskim analitykiem BTC. TYLKO JSON, po polsku. Uzupełnij brakujące klucze.",
            "Uzupełnij TYLKO te pola: "
            + json.dumps(missing_fields)
            + ".\nBrief:\n"
            + ctx_json,
            model_override=base_model,
            max_tokens=900,
            force_json=True,
        ) or {}
        for k, v in patch.items():
            if k in missing_fields and v:
                conclusions[k] = v

    if not conclusions.get("obraz_rynku") and not conclusions.get("wnioski"):
        conclusions = _outlook_emergency(brief)
    else:
        # uzupełnij puste pola syntezą z danych, bez kasowania udanych części AI
        emergency = _outlook_emergency(brief)
        for k, v in emergency.items():
            cur = conclusions.get(k)
            empty = cur in (None, "", [], {}) or (
                isinstance(cur, dict) and not any(cur.values())
            )
            if empty and v:
                conclusions[k] = v

    out = _normalize(
        {
            "tytul_dnia": conclusions.get("tytul_dnia") or "",
            "glowne_watki": conclusions.get("glowne_watki") or [],
            "trendy_rynku": conclusions.get("trendy_rynku") or [],
            "makro_a_btc": conclusions.get("makro_a_btc") or "",
            "wieloryby": conclusions.get("wieloryby") or "",
            "obraz_rynku": conclusions.get("obraz_rynku") or "",
            "scenariusze_btc": conclusions.get("scenariusze_btc") or {},
            "na_radarze": conclusions.get("na_radarze") or [],
            "wnioski": conclusions.get("wnioski")
            or [
                (brief.get("market") or {}).get("halving_note") or "Brak pełnych wniosków AI.",
            ],
            "crypto_pl": news_pl.get("crypto_pl") or [],
            "crypto_en": news_en.get("crypto_en") or [],
            "macro": news_macro.get("macro") or [],
            "forum": forum,
            "ryzyka": conclusions.get("ryzyka") or [],
        },
        brief,
    )
    out["ai_enabled"] = True
    resolved = _resolve_ai()
    assert resolved
    _, base, _ = resolved
    out["ai_provider"] = "groq" if "groq.com" in base else "openrouter"
    out["ai_model"] = news_model
    out["crypto_en"] = [_ensure_pl_en_item(x) for x in out.get("crypto_en") or []]
    out["crypto_pl"] = [
        {**x, "summary_pl": _finish_sentence(x.get("summary_pl") or "")} for x in out.get("crypto_pl") or []
    ]
    # Uzupełnij brakujące artykuły z briefu
    out["crypto_pl"] = _fill_missing(out["crypto_pl"], pl_all, kind="pl")
    out["crypto_en"] = _fill_missing(out["crypto_en"], en_all, kind="en")
    return out


def analyze_period(brief: dict[str, Any]) -> dict[str, Any]:
    """Osobna ścieżka AI dla weekly/monthly/… — nie kopia daily news."""
    if not _resolve_ai():
        return _period_fallback(brief)

    news_model = cfg.env("AI_MODEL_NEWS") or "qwen/qwen3.8-27b"
    base_model = cfg.env("AI_MODEL") or "openai/gpt-oss-20b"
    period = brief.get("period") or {}
    primary = (brief.get("report_types") or ["monthly"])[0]
    pctx = brief.get("period_context") or {}
    price = pctx.get("price_path") or brief.get("period_history") or {}
    focus = _period_focus_instruction(primary, period)

    def _slim_arts(items: list[dict], n: int = 18) -> list[dict]:
        out = []
        for a in items[:n]:
            out.append(
                {
                    "title": (a.get("title") or "")[:160],
                    "source": a.get("source"),
                    "published_at": a.get("published_at"),
                    "summary": (a.get("summary") or "")[:220],
                    "is_wrap": bool(a.get("is_wrap")),
                }
            )
        return out

    ctx = {
        "report_type": primary,
        "period": period,
        "price_path": {
            k: price.get(k)
            for k in (
                "btc_start",
                "btc_end",
                "btc_min",
                "btc_max",
                "btc_change_pct",
                "eth_start",
                "eth_end",
                "eth_change_pct",
                "samples",
                "source",
                "note",
            )
        },
        "whales": {
            k: (brief.get("whales") or {}).get(k)
            for k in ("net_24h_btc", "net_7d_btc", "bias_24h", "bias_7d", "note")
        },
        "market_now": {
            k: (brief.get("market") or {}).get(k)
            for k in (
                "btc_usd",
                "eth_usd",
                "fear_greed",
                "fear_greed_label",
                "btc_dominance_pct",
                "etf_latest_flow_musd",
                "us10y_yield",
            )
        },
        "wydarzenia_i_newsy_okresu": _slim_arts(pctx.get("period_headlines") or [], 22),
        "cudze_recapy_okresu": _slim_arts(pctx.get("period_wraps") or [], 10),
        "archiwum_lokalne": _slim_arts(pctx.get("archived_articles") or [], 12),
    }
    ctx_json = json.dumps(ctx, ensure_ascii=False)

    def _part(label: str, system: str, user: str) -> dict:
        print(f"  · {label}…")
        time.sleep(22)
        part = _chat_json(system, user, model_override=news_model, max_tokens=900, force_json=False) or {}
        if not part:
            time.sleep(18)
            part = _chat_json(system, user, model_override=base_model, max_tokens=900, force_json=False) or {}
        return part

    part1 = _part(
        f"AI okres 1/3 — wydarzenia ({primary})",
        "Jesteś polskim analitykiem rynku krypto. TYLKO JSON, po polsku. "
        + focus
        + " Wybierz 5-8 NAJWAŻNIEJSZYCH wydarzeń CAŁEGO okresu (nie tylko ostatniego dnia). "
        "Hacki giełd, ETF, regulacje, duże ruchy ceny mają pierwszeństwo. "
        "Możesz inspirować się cudzymi recapami z briefu (cudze_recapy_okresu), ale pisz własnymi słowami. "
        "Schema: "
        '{"tytul_dnia":"tytuł całego okresu",'
        '"kluczowe_wydarzenia":[{"tytul":"...","kiedy":"...","dlaczego_wazne":"2-3 zdania"}],'
        '"inspiracje_z_recapow":[{"zrodlo_lub_watek":"...","wniosek":"1-2 zdania"}]}',
        "Zbriefu wypisz kluczowe wydarzenia i wnioski z cudzych podsumowań:\n" + ctx_json,
    )
    part2 = _part(
        f"AI okres 2/3 — trajektoria i obraz ({primary})",
        "Jesteś polskim analitykiem BTC. TYLKO JSON, po polsku. "
        + focus
        + " Użyj price_path (start/min/max/koniec). Schema: "
        '{"trajektoria":"3-5 zdań o cenie BTC/ETH w okresie",'
        '"obraz_rynku":"4-6 zdań synteza okresu",'
        '"makro_a_btc":"3-4 zdania",'
        '"wieloryby":"2-4 zdania o przepływach w kontekście okresu",'
        '"tematy_dyskusji":["temat forów/społeczności 1","…"]}',
        "Opisz trajektorię i obraz całego okresu:\n" + ctx_json,
    )
    part3 = _part(
        f"AI okres 3/3 — wnioski ({primary})",
        "Jesteś polskim analitykiem BTC. TYLKO JSON, po polsku. "
        + focus
        + " Schema: "
        '{"scenariusze_btc":{"bazowy":"...","optymistyczny":"...","pesymistyczny":"..."},'
        '"na_radarze":["..."],"ryzyka":["..."],"wnioski":["..."]}',
        "Wnioski i scenariusze NA BAZIE całego okresu (nie tylko dziś):\n" + ctx_json,
    )

    conclusions = {**part1, **part2, **part3}
    if part3.get("scenariusze_btc"):
        conclusions["scenariusze_btc"] = part3["scenariusze_btc"]

    # mapowanie na pola szablonu + pola okresowe
    glowne = conclusions.get("kluczowe_wydarzenia") or conclusions.get("glowne_watki") or []
    if glowne and isinstance(glowne[0], dict) and "watek" not in glowne[0]:
        glowne = [
            {
                "watek": x.get("tytul") or x.get("watek") or "wydarzenie",
                "opis": " ".join(
                    p for p in [x.get("kiedy") and f"({x.get('kiedy')})", x.get("dlaczego_wazne") or x.get("opis")] if p
                ),
            }
            for x in glowne
            if isinstance(x, dict)
        ]

    out = _normalize(
        {
            "tytul_dnia": conclusions.get("tytul_dnia") or conclusions.get("tytul_okresu") or period.get("label_pl") or "",
            "glowne_watki": glowne,
            "trendy_rynku": conclusions.get("trendy_rynku") or [],
            "makro_a_btc": conclusions.get("makro_a_btc") or "",
            "wieloryby": conclusions.get("wieloryby") or "",
            "obraz_rynku": conclusions.get("obraz_rynku") or conclusions.get("trajektoria") or "",
            "scenariusze_btc": conclusions.get("scenariusze_btc") or {},
            "na_radarze": conclusions.get("na_radarze") or [],
            "wnioski": conclusions.get("wnioski") or [],
            "ryzyka": conclusions.get("ryzyka") or [],
            "crypto_pl": [],
            "crypto_en": [],
            "macro": [],
            "forum": {
                "overall": "",
                "hot_topics": conclusions.get("tematy_dyskusji") or [],
                "cold_or_fading": [],
                "summaries": [],
            },
        },
        brief,
    )
    out["is_period_report"] = True
    out["trajektoria"] = conclusions.get("trajektoria") or ""
    out["kluczowe_wydarzenia"] = conclusions.get("kluczowe_wydarzenia") or []
    out["inspiracje_z_recapow"] = conclusions.get("inspiracje_z_recapow") or []
    out["tematy_dyskusji"] = conclusions.get("tematy_dyskusji") or []
    out["period_headlines"] = (pctx.get("period_headlines") or [])[:15]
    out["period_wraps"] = (pctx.get("period_wraps") or [])[:8]
    out["price_path"] = price
    out["ai_enabled"] = True
    resolved = _resolve_ai()
    assert resolved
    _, base, _ = resolved
    out["ai_provider"] = "groq" if "groq.com" in base else "openrouter"
    out["ai_model"] = news_model
    return out


def _period_fallback(brief: dict[str, Any]) -> dict[str, Any]:
    period = brief.get("period") or {}
    pctx = brief.get("period_context") or {}
    price = pctx.get("price_path") or brief.get("period_history") or {}
    heads = pctx.get("period_headlines") or []
    events = [
        {
            "tytul": h.get("title"),
            "kiedy": (h.get("published_at") or "")[:10],
            "dlaczego_wazne": (h.get("summary") or "Wydarzenie z okresu — AI niedostępne, surowy tytuł."),
        }
        for h in heads[:6]
    ]
    chg = price.get("btc_change_pct")
    traj = (
        f"BTC w okresie: {price.get('btc_start')} → {price.get('btc_end')} USD "
        f"(min {price.get('btc_min')}, max {price.get('btc_max')}, zmiana {chg}%)."
        if price.get("btc_start")
        else "Brak pełnej trajektorii ceny."
    )
    return {
        "ai_enabled": False,
        "is_period_report": True,
        "tytul_dnia": period.get("label_pl") or "Podsumowanie okresu",
        "glowne_watki": [
            {"watek": e.get("tytul"), "opis": e.get("dlaczego_wazne")} for e in events if e.get("tytul")
        ],
        "kluczowe_wydarzenia": events,
        "inspiracje_z_recapow": [],
        "trajektoria": traj,
        "obraz_rynku": traj,
        "makro_a_btc": "",
        "wieloryby": (brief.get("whales") or {}).get("note") or "",
        "trendy_rynku": [],
        "scenariusze_btc": {},
        "na_radarze": [],
        "wnioski": [traj],
        "ryzyka": [],
        "tematy_dyskusji": [],
        "forum": {"overall": "", "hot_topics": [], "summaries": []},
        "crypto_pl": [],
        "crypto_en": [],
        "macro": [],
        "period_headlines": heads[:15],
        "period_wraps": (pctx.get("period_wraps") or [])[:8],
        "price_path": price,
        "error": "Brak AI — surowy digest okresu.",
    }


def merge_enriched_articles(
    original: dict[str, list[dict]],
    analysis: dict[str, Any],
) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"crypto_pl": [], "crypto_en": [], "macro": []}
    pl_map = {x.get("link"): x for x in analysis.get("crypto_pl") or [] if x.get("link")}
    en_map = {x.get("link"): x for x in analysis.get("crypto_en") or [] if x.get("link")}
    macro_map = {x.get("link"): x for x in analysis.get("macro") or [] if x.get("link")}

    for a in original.get("crypto_pl", []):
        e = pl_map.get(a["link"], {})
        item = dict(a)
        item["summary_pl"] = _finish_sentence(e.get("summary_pl") or a.get("summary") or "")
        item["title_pl"] = e.get("title") or a.get("title")
        out["crypto_pl"].append(item)

    for a in original.get("crypto_en", []):
        e = en_map.get(a["link"], {})
        item = dict(a)
        item["title_en"] = e.get("title_en") or a.get("title")
        item["title_pl"] = e.get("title_pl") or "(brak tłumaczenia w tej rundzie)"
        item["summary_pl"] = _finish_sentence(e.get("summary_pl") or "Brak polskiego streszczenia.")
        out["crypto_en"].append(_ensure_pl_en_item(item))

    for a in original.get("macro", []):
        e = macro_map.get(a["link"], {})
        item = dict(a)
        item["title_pl"] = e.get("title_pl") or e.get("title") or a.get("title")
        item["summary_pl"] = _finish_sentence(e.get("summary_pl") or a.get("summary") or "")
        out["macro"].append(item)
    return out


def _missing_en_translations(original: list[dict], done: list[dict]) -> list[dict]:
    have = {}
    for x in done:
        link = x.get("link")
        if not link:
            continue
        title_pl = (x.get("title_pl") or "").strip()
        summary_pl = (x.get("summary_pl") or "").strip()
        bad = (
            not title_pl
            or title_pl.startswith("(brak")
            or title_pl.startswith("[EN→PL")
            or _looks_english(title_pl)
            or not summary_pl
            or summary_pl.startswith("Brak polskiego")
            or _looks_english(summary_pl)
        )
        if not bad:
            have[link] = x
    missing = []
    for a in original:
        if a.get("link") not in have:
            missing.append(a)
    return missing


def _merge_en_by_link(base: list[dict], extra: list[dict]) -> list[dict]:
    by_link = {x.get("link"): dict(x) for x in base if x.get("link")}
    for x in extra:
        link = x.get("link")
        if not link:
            continue
        cur = by_link.get(link, {})
        cur.update({k: v for k, v in x.items() if v})
        by_link[link] = cur
    # keep original order from base, then extras
    out = []
    seen = set()
    for x in base:
        link = x.get("link")
        if link and link in by_link:
            out.append(by_link[link])
            seen.add(link)
    for link, x in by_link.items():
        if link not in seen:
            out.append(x)
    return out


def _forum_fallback_pl(threads: list[dict]) -> dict[str, Any]:
    return {
        "overall": (
            "Pełna narracja AI forów nie doszła (limit Groq). "
            "Poniżej wątki z BitcoinTalk — otwórz linki; streszczenia to surowe fragmenty (mogą być po angielsku)."
        ),
        "hot_topics": [t.get("title") for t in threads[:4] if t.get("title")],
        "cold_or_fading": [],
        "summaries": [
            {
                "link": t.get("link"),
                "title": t.get("title"),
                "title_pl": t.get("title"),
                "summary_pl": _finish_sentence(t.get("excerpt") or "Brak fragmentu posta."),
            }
            for t in threads
        ],
    }


def _fill_missing(done: list[dict], original: list[dict], kind: str) -> list[dict]:
    have = {x.get("link") for x in done}
    out = list(done)
    for a in original:
        if a.get("link") in have:
            continue
        if kind == "en":
            out.append(
                {
                    "link": a.get("link"),
                    "title_en": a.get("title"),
                    "title_pl": "(brak tłumaczenia — limit AI)",
                    "summary_pl": a.get("excerpt") or a.get("summary") or "",
                }
            )
        else:
            out.append(
                {
                    "link": a.get("link"),
                    "title": a.get("title"),
                    "summary_pl": a.get("excerpt") or a.get("summary") or a.get("title"),
                }
            )
    return out


def _finish_sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return text
    # Obetnij do ostatniego kompletnego zdania, jeśli urwane w połowie słowa/zdania
    if text[-1] not in ".!?…":
        for sep in (". ", "! ", "? "):
            idx = text.rfind(sep)
            if idx >= 60:
                return text[: idx + 1].strip()
        return text.rstrip(",;: ") + "…"
    # Jeśli kończy się kropką, ale ostatnie "zdanie" wygląda na ucięte słowo przed nią
    words = text.rstrip(".!?…").split()
    if words and len(words[-1]) <= 3 and not words[-1].lower() in {"btc", "eth", "usa", "ue", "nft", "etf"}:
        for sep in (". ", "! ", "? "):
            idx = text.rfind(sep)
            if idx >= 60:
                return text[: idx + 1].strip()
    return text


def _resolve_ai() -> tuple[str, str, str] | None:
    groq = cfg.env("GROQ_API_KEY")
    if groq:
        model = cfg.env("AI_MODEL") or "openai/gpt-oss-20b"
        base = (cfg.env("AI_BASE_URL") or "https://api.groq.com/openai/v1").rstrip("/")
        return groq, base, model
    openrouter = cfg.env("OPENROUTER_API_KEY")
    if openrouter:
        model = cfg.env("AI_MODEL") or "openai/gpt-4o-mini"
        base = (cfg.env("AI_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
        return openrouter, base, model
    return None


def _outlook_emergency(brief: dict[str, Any]) -> dict[str, Any]:
    """Synteza z liczb gdy Groq nie dociągnie pełnego outlooku — dłuższa niż stary stub."""
    w = brief.get("whales") or {}
    m = brief.get("market") or {}
    pe = [e for e in (w.get("per_exchange_24h") or []) if e.get("has_data")]
    pe_txt = ", ".join(
        f"{e.get('exchange')} {float(e.get('net_btc') or 0):+.1f} BTC" for e in pe[:5]
    ) or "brak rozbicia per giełda w free feed"
    onchain = w.get("large_onchain") or []
    on_txt = ", ".join(f"{x.get('amount_btc')} BTC" for x in onchain[:3]) or "brak dużych TX w próbce"
    wa = w.get("whale_alert_txs") or []
    wa_txt = f"{len(wa)} alertów Whale Alert" if wa else "Whale Alert niepodłączony (brak WHALE_ALERT_API_KEY)"
    return {
        "tytul_dnia": f"BTC ≈ {m.get('btc_usd')} USD — synteza z danych (limit AI)",
        "glowne_watki": [
            {
                "watek": "Cena i sentyment",
                "opis": (
                    f"BTC około {m.get('btc_usd')} USD (zmiana 24h: {m.get('change_24h_pct')}%, "
                    f"7d: {m.get('change_7d_pct')}%). Fear & Greed: {m.get('fear_greed')}. "
                    "Pełna narracja AI nie doszła — poniżej synteza z twardych wskaźników raportu."
                ),
            },
            {
                "watek": "ETF i hashrate",
                "opis": (
                    f"{m.get('etf_flow_note') or 'Sprawdź sekcję ETF.'} "
                    f"Hashrate / mining: {m.get('hashrate_note') or 'w sekcji rynku'}. "
                    f"{m.get('halving_note') or ''}"
                ),
            },
            {
                "watek": "Wieloryby / giełdy",
                "opis": (
                    f"{w.get('note') or 'Brak netflow.'} Per giełda: {pe_txt}. "
                    f"On-chain (mempool): {on_txt}. {wa_txt}."
                ),
            },
        ],
        "trendy_rynku": [
            {
                "trend": "Cena vs USD",
                "kierunek": "mieszany",
                "opis": f"24h {m.get('change_24h_pct')}%, 7d {m.get('change_7d_pct')}%, 30d {m.get('change_30d_pct')}%.",
            },
            {
                "trend": "Netflow giełdowy",
                "kierunek": "mieszany",
                "opis": f"Bias 24h: {w.get('bias_24h')}. Bias 7d: {w.get('bias_7d')}.",
            },
        ],
        "makro_a_btc": (
            f"US10Y ≈ {m.get('us10y_yield')}%, Fed funds ≈ {m.get('fed_funds')}. "
            "Sprawdź sekcję makro/newsy w raporcie — pełna interpretacja AI była ograniczona limitem tokenów."
        ),
        "wieloryby": (
            f"{w.get('note') or ''} Rozbicie: {pe_txt}. "
            f"Brak danych free feed: {', '.join(w.get('missing_exchanges') or []) or '—'}. "
            f"Duże on-chain: {on_txt}. {wa_txt}."
        ),
        "obraz_rynku": (
            f"BTC {m.get('btc_usd')} USD, F&G {m.get('fear_greed')}. "
            f"ETF: {m.get('etf_flow_note')}. Halving: {m.get('halving_note')}. "
            f"Netflow 24h: {w.get('net_24h_btc')} BTC ({w.get('bias_24h')}). "
            "To skrót awaryjny — odtwórz raport za kilka minut przy wolniejszym limicie Groq, aby dostać pełne akapity AI."
        ),
        "scenariusze_btc": {
            "bazowy": (
                "Przy obecnym biasie netflow i odczytach ETF rynek może iść bokiem lub z umiarkowaną zmiennością, "
                "dopóki nie pojawi się silny impuls makro/ETF. Pełne scenariusze AI niedostępne w tej rundzie."
            ),
            "optymistyczny": "Silniejsze napływy ETF + odpływ BTC z giełd mogłyby wspierać kontynuację wzrostu.",
            "pesymistyczny": "Napływ BTC na giełdy przy słabym ETF/makro zwiększałby ryzyko korekty.",
        },
        "na_radarze": ["ETF flows", "Netflow giełd", "Makro/US10Y", "Duże tx on-chain", "Fear & Greed"],
        "ryzyka": [
            "Limit darmowego AI ograniczył pełną narrację.",
            "Free feed netflow nie pokrywa wszystkich giełd.",
            "Duże TX on-chain bez etykiet nie oznaczają automatycznie sprzedaży.",
        ],
        "wnioski": [
            f"BTC ≈ {m.get('btc_usd')} USD | F&G {m.get('fear_greed')}",
            f"Bias wielorybów 24h: {w.get('bias_24h')}",
            f"Bias 7d: {w.get('bias_7d')}",
            f"Per giełda: {pe_txt}",
            f"On-chain large: {on_txt}",
            wa_txt,
        ],
    }


def _slim_en_for_translate(items: list[dict]) -> list[dict]:
    """Krótki input = krótszy, mniej urwany JSON na wyjściu."""
    slim = []
    for a in items:
        slim.append(
            {
                "link": a.get("link"),
                "title": (a.get("title") or "")[:180],
                "source": a.get("source"),
                "excerpt": (a.get("excerpt") or a.get("summary") or "")[:220],
            }
        )
    return slim


def _coerce_en_items(raw: Any, fallback_link: str | None = None) -> list[dict]:
    """Znormalizuj różne kształty odpowiedzi modelu do listy crypto_en."""
    if raw is None:
        return []
    items: list[Any]
    if isinstance(raw, dict):
        if isinstance(raw.get("crypto_en"), list):
            items = raw["crypto_en"]
        elif "title_pl" in raw or "summary_pl" in raw or "title" in raw:
            items = [raw]
        else:
            items = []
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    out: list[dict] = []
    for x in items:
        if not isinstance(x, dict):
            continue
        link = x.get("link") or fallback_link
        title_en = x.get("title_en") or x.get("title") or ""
        title_pl = (x.get("title_pl") or x.get("tytul_pl") or "").strip()
        summary_pl = (x.get("summary_pl") or x.get("streszczenie") or x.get("summary") or "").strip()
        if not link or (not title_pl and not summary_pl):
            continue
        out.append(
            {
                "link": link,
                "title_en": title_en,
                "title_pl": title_pl or title_en,
                "summary_pl": summary_pl,
            }
        )
    return out


def _translate_one_en(one: dict, primary_model: str, fallback_model: str) -> dict:
    """Jeden artykuł EN→PL; przy porażce drugi model, potem awaryjny opis PL."""
    print(f"    · EN→PL pojedynczo: {(one.get('title') or '')[:48]}…")
    prompt = (
        "Przetłumacz ten JEDEN artykuł na polski. "
        "Zwróć WYŁĄCZNIE JSON:\n"
        '{"crypto_en":[{"link":"'
        + str(one.get("link") or "")
        + '","title_en":"...","title_pl":"...","summary_pl":"Zdanie1. Zdanie2. Zdanie3."}]}\n'
        "title_pl i summary_pl muszą być po polsku. summary_pl = 3 krótkie zdania.\n"
        + json.dumps({"crypto_en": [one]}, ensure_ascii=False)
    )
    for model in (primary_model, fallback_model):
        part = _chat_json(
            NEWS_SYSTEM,
            prompt,
            model_override=model,
            max_tokens=450,
        ) or {}
        got = _coerce_en_items(part, fallback_link=one.get("link"))
        if not got and part:
            got = _coerce_en_items(part.get("crypto_en"), fallback_link=one.get("link"))
        good = [x for x in got if not _missing_en_translations([one], [x])]
        if good:
            return good[0]
        if got and not _looks_english(got[0].get("summary_pl") or ""):
            return got[0]
        time.sleep(14)
    # awaria AI — jasny komunikat PL (bez wklejania długiego EN jako „tłumaczenia”)
    title = (one.get("title") or "").strip()
    src = one.get("source") or "źródło zagraniczne"
    return {
        "link": one.get("link"),
        "title_en": title,
        "title_pl": f"Wiadomość z {src}: {title}" if title else f"Wiadomość z {src}",
        "summary_pl": (
            f"Automatyczne tłumaczenie tego materiału z {src} nie doszło z powodu limitu AI. "
            f"Tytuł oryginału: {title}. "
            "Otwórz link źródłowy, aby przeczytać pełną treść po angielsku."
        ),
    }


def _repair_json_text(content: str) -> str:
    content = (content or "").strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    start = content.find("{")
    if start < 0:
        return content
    content = content[start:]
    # trailing commas before } or ]
    content = re.sub(r",\s*([}\]])", r"\1", content)
    try:
        json.loads(content)
        return content
    except json.JSONDecodeError:
        pass
    # domknij ucięty JSON (częsty przy OTPM)
    in_str = False
    escape = False
    stack: list[str] = []
    for ch in content:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack and ch == stack[-1]:
            stack.pop()
    if in_str:
        content += '"'
    # obetnij wiszący przecinek / dwukropek
    content = content.rstrip()
    while content and content[-1] in ",:":
        content = content[:-1].rstrip()
    content += "".join(reversed(stack))
    content = re.sub(r",\s*([}\]])", r"\1", content)
    return content


def _extract_json(content: str) -> dict[str, Any]:
    repaired = _repair_json_text(content)
    try:
        data = json.loads(repaired)
        if isinstance(data, dict):
            return data
        raise json.JSONDecodeError("root not object", repaired, 0)
    except json.JSONDecodeError:
        start = repaired.find("{")
        end = repaired.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(repaired[start : end + 1])
            if isinstance(data, dict):
                return data
        raise


def _chat_json(
    system: str,
    user: str,
    model_override: str | None = None,
    max_tokens: int = 900,
    force_json: bool = True,
) -> dict[str, Any] | None:
    resolved = _resolve_ai()
    if not resolved:
        return None
    api_key, base, model = resolved
    model = model_override or model
    # Groq free OTPM bywa ~1000 — nie proś o więcej
    max_tokens = min(int(max_tokens), 900)
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user[:12000]},
        ],
    }
    if force_json:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            with httpx.Client(timeout=180.0) as client:
                r = client.post(f"{base}/chat/completions", headers=headers, json=payload)
                if r.status_code == 429:
                    wait_s = 35 + attempt * 20
                    print(f"! Groq rate limit — czekam {wait_s}s…")
                    time.sleep(wait_s)
                    last_err = RuntimeError(r.text[:300])
                    continue
                if r.status_code >= 400:
                    # Groq: json_validate_failed przy uciętym output — od razu bez response_format
                    body = r.text[:500]
                    if r.status_code == 400 and "json" in body.lower():
                        last_err = RuntimeError(f"AI HTTP {r.status_code}: {body[:300]}")
                        print(f"! AI JSON invalid ({model}), retry bez force_json…")
                        payload.pop("response_format", None)
                        payload["max_tokens"] = min(int(payload.get("max_tokens") or 700), 600)
                        time.sleep(10 + attempt * 6)
                        continue
                    raise RuntimeError(f"AI HTTP {r.status_code}: {body[:400]}")
                raw = r.json()["choices"][0]["message"]["content"]
                try:
                    return _extract_json(raw)
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as jexc:
                    last_err = jexc
                    print(f"! AI JSON uszkodzony ({model}), retry {attempt + 1}/5: {jexc}")
                    payload.pop("response_format", None)
                    time.sleep(12 + attempt * 8)
                    continue
        except Exception as exc:
            last_err = exc
            print(f"! AI błąd ({model}): {exc}")
            if "429" in str(exc) or "rate" in str(exc).lower():
                time.sleep(35 + attempt * 15)
                continue
            if "json" in str(exc).lower():
                payload.pop("response_format", None)
                time.sleep(10)
                continue
            time.sleep(8)
            continue
    if last_err:
        print(f"! AI pominięte: {last_err}")
    return None


def _looks_english(text: str) -> bool:
    if not text:
        return False
    en = len(re.findall(r"\b(the|and|with|from|that|this|for|are|was|have|will)\b", text.lower()))
    pl = len(re.findall(r"\b(oraz|który|która|według|rynku|jest|nie|się|oraz)\b", text.lower()))
    return en >= 3 and en > pl


def _ensure_pl_en_item(item: dict[str, Any]) -> dict[str, Any]:
    item = dict(item)
    if _looks_english(item.get("title_pl") or ""):
        item["title_pl"] = f"[EN→PL słabe] {item.get('title_pl')}"
    if _looks_english(item.get("summary_pl") or ""):
        item["summary_pl"] = "Streszczenie wróciło po angielsku — treść: " + (item.get("summary_pl") or "")
    item["summary_pl"] = _finish_sentence(item.get("summary_pl") or "")
    return item


def _slim_articles(items: list[dict], n: int = 5, summary_chars: int = 180) -> list[dict]:
    slim = []
    for a in items[:n]:
        slim.append(
            {
                "title": a.get("title"),
                "link": a.get("link"),
                "source": a.get("source"),
                "lang": a.get("lang"),
                "summary": (a.get("summary") or "")[:summary_chars],
                "excerpt": (a.get("excerpt") or "")[:280],
            }
        )
    return slim


def _normalize(data: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    data.setdefault("tytul_dnia", "")
    data.setdefault("glowne_watki", [])
    data.setdefault("trendy_rynku", [])
    data.setdefault("makro_a_btc", "")
    data.setdefault("wieloryby", "")
    data.setdefault("obraz_rynku", "")
    data.setdefault("scenariusze_btc", {})
    data.setdefault("na_radarze", [])
    data.setdefault("wnioski", [])
    data.setdefault("crypto_pl", [])
    data.setdefault("crypto_en", [])
    data.setdefault("macro", [])
    data.setdefault("forum", {})
    data.setdefault("ryzyka", [])
    if isinstance(data["forum"], str):
        data["forum"] = {"overall": data["forum"], "hot_topics": [], "cold_or_fading": [], "summaries": []}
    if not isinstance(data.get("scenariusze_btc"), dict):
        data["scenariusze_btc"] = {}
    for key in ("bazowy", "optymistyczny", "pesymistyczny"):
        data["scenariusze_btc"].setdefault(key, "")
    forum = data["forum"]
    forum.setdefault("overall", "")
    forum.setdefault("hot_topics", [])
    forum.setdefault("cold_or_fading", [])
    forum.setdefault("summaries", [])
    return data


def _fallback(brief: dict[str, Any]) -> dict[str, Any]:
    m = brief.get("market") or {}
    return {
        "wnioski": [
            f"BTC ≈ {m.get('btc_usd')} USD. Halving: {m.get('halving_note')}",
            "AI niedostępne — ustaw GROQ_API_KEY.",
        ],
        "crypto_pl": [
            {"link": a.get("link"), "title": a.get("title"), "summary_pl": a.get("summary") or a.get("title")}
            for a in brief.get("crypto_pl", [])
        ],
        "crypto_en": [
            {
                "link": a.get("link"),
                "title_en": a.get("title"),
                "title_pl": "(brak tłumaczenia)",
                "summary_pl": a.get("summary") or "",
            }
            for a in brief.get("crypto_en", [])
        ],
        "macro": [
            {
                "link": a.get("link"),
                "title": a.get("title"),
                "title_pl": a.get("title"),
                "summary_pl": a.get("summary") or "",
            }
            for a in brief.get("macro", [])
        ],
        "forum": _forum_fallback_pl(brief.get("forum") or []),
        "ryzyka": ["Analiza AI wyłączona."],
        "ai_enabled": False,
    }
