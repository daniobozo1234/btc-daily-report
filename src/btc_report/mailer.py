from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path
from typing import Any

import resend

from btc_report import config as cfg
from btc_report.periods import LABELS
from btc_report.publish import archive_url_from

# Losowe tipy w stylu CoD — (cytat, autor)
WISE_QUOTES: list[tuple[str, str]] = [
    ("Nawet zegar stojący ma rację dwa razy dziennie.", "przysłowie"),
    ("Kto pyta, nie błądzi — najwyżej dostanie złą odpowiedź.", "anonim"),
    ("Czasem najlepszą pozycją jest brak pozycji.", "traderzy rynkowi (parafraza)"),
    ("Nie goni się spadającego noża.", "powiedzenie giełdowe"),
    ("Plan B istnieje po to, by Plan A wyglądał pewniej.", "anonim"),
    ("Rynek może pozostać irracjonalny dłużej, niż Ty wypłacalny.", "John Maynard Keynes (parafraza)"),
    ("Jeśli wszyscy biegną w jedną stronę, sprawdź, czy nie na przepaść.", "anonim"),
    ("Cisza też jest informacją.", "analitycy / wywiad (parafraza)"),
    ("Nie musisz przewidywać przyszłości. Wystarczy nie być zaskoczony teraźniejszością.", "anonim"),
    ("Cierpliwość to strategia. Panika to tylko głośniejszy błąd.", "anonim"),
    ("Mapa to nie teren.", "Alfred Korzybski (parafraza)"),
    ("Szczęście lubi przygotowanych.", "Ludwik Pasteur (parafraza)"),
    ("Nie każda zielona świeca oznacza wiosnę.", "traderzy (żart rynkowy)"),
    ("Wojna wygrywa się logistyką.", "Napoleon Bonaparte (parafraza)"),
    ("Historia się nie powtarza, ale rymuje.", "Mark Twain (parafraza)"),
    ("Dywersyfikacja: sztuka mylenia się na wiele sposobów naraz.", "żart inwestycyjny"),
    ("Gdy brakuje danych, wyobraźnia sprzedaje pewność.", "anonim"),
    ("Zrób przerwę. Wykres poczeka.", "anonim"),
    ("Lepiej wiedzieć mniej pewnie niż pewnie źle.", "anonim"),
    ("Czasem wycofanie to jedyny ruch do przodu.", "strategia / sport (parafraza)"),
    ("Oddychaj. Rynek nie zna Twojego rytmu snu.", "anonim"),
    ("Im więcej wykresów, tym bardziej chce się kliknąć „odśwież”.", "każdy daytrader"),
    ("Nie wszystko, co się rusza, zasługuje na uwagę.", "anonim"),
    ("Zanim kupisz nadzieję, sprawdź spread.", "traderzy"),
    ("Dobry dzień zaczyna się od sprawdzenia faktów, nie plotek.", "anonim"),
]


def send_email(
    *,
    subject: str,
    html: str = "",
    chart_path: Path | None = None,
    chart_paths: list[Path] | None = None,
    tradingview_url: str | None = None,
    tradingview_eth_url: str | None = None,
    full_report_html: str | None = None,
    full_report_name: str = "btc_eth_report.html",
    report_types: list[str] | None = None,
    report_date: datetime | None = None,
    report_url: str | None = None,
) -> dict[str, Any]:
    """Wysyła krótki mail z linkiem do pełnego raportu (bez ciężkiego HTML w treści)."""
    api_key = cfg.require_env("RESEND_API_KEY")
    to_emails = _parse_recipients(cfg.require_env("REPORT_TO_EMAIL"))
    if not to_emails:
        raise RuntimeError("REPORT_TO_EMAIL jest puste — podaj co najmniej jeden adres.")
    from_email = (
        cfg.env("REPORT_FROM_EMAIL", "BTC Report <onboarding@resend.dev>")
        or "BTC Report <onboarding@resend.dev>"
    )

    cover = build_cover_html(
        report_types=report_types or ["daily"],
        report_date=report_date or datetime.now(),
        report_url=report_url,
    )

    resend.api_key = api_key
    params: dict[str, Any] = {
        "from": from_email,
        "to": to_emails,
        "subject": subject,
        "html": cover,
    }
    print(f"→ odbiorcy: {', '.join(to_emails)}")
    return resend.Emails.send(params)


def _parse_recipients(raw: str) -> list[str]:
    """Jeden lub wiele adresów: przecinek / średnik / nowa linia."""
    parts = raw.replace(";", ",").replace("\n", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def build_cover_html(
    *,
    report_types: list[str],
    report_date: datetime,
    report_url: str | None = None,
    attachment_name: str | None = None,
    html_inside: str | None = None,
) -> str:
    period = describe_period(report_types, report_date)
    quote, author = random.choice(WISE_QUOTES)

    if report_url:
        archive_url = archive_url_from(report_url)
        archive_line = ""
        if archive_url:
            archive_line = f"""
  <p style="color:#666;font-size:0.9rem">
    Poprzednie dni (bez ściągania):<br />
    <a href="{archive_url}">{archive_url}</a>
  </p>
"""
        body_main = f"""
  <p>
    poniżej masz <b>link do pełnego raportu</b> {period}.
  </p>
  <p style="margin:20px 0">
    <a href="{report_url}"
       style="display:inline-block;background:#111;color:#fff;padding:12px 18px;text-decoration:none;border-radius:4px">
      Otwórz pełny raport
    </a>
  </p>
  <p style="color:#666;font-size:0.9rem">
    Ten link jest na ten dzień i się nie nadpisuje.<br />
    <a href="{report_url}">{report_url}</a>
  </p>
  {archive_line}
"""
    else:
        body_main = f"""
  <p>
    pełny raport {period} nie ma jeszcze publicznego linku
    (trzeba raz podpiąć GitHub — wtedy w mailu będzie przycisk „Otwórz pełny raport”).
  </p>
"""

    return f"""<!DOCTYPE html>
<html lang="pl">
<head><meta charset="utf-8" /></head>
<body style="font-family: Georgia, 'Times New Roman', serif; color:#1a1a1a; line-height:1.5; max-width:560px; margin:24px auto; padding:0 16px">
  <p>Cześć Paweł,</p>
  {body_main}
  <p style="color:#666;font-size:0.95rem">
    To materiał informacyjny, nie rekomendacja inwestycyjna.
  </p>
  <hr style="border:none;border-top:1px solid #ddd;margin:28px 0 16px" />
  <p style="font-style:italic;color:#444;font-size:0.95rem">
    „{quote}”
  </p>
  <p style="color:#666;font-size:0.85rem;margin-top:6px">— {author}</p>
  <p style="color:#999;font-size:0.8rem;margin-top:8px">losowy tip dnia</p>
</body>
</html>
"""


def describe_period(report_types: list[str], report_date: datetime) -> str:
    """Opis okresu po polsku do treści maila."""
    day = report_date.strftime("%d.%m.%Y")
    types = report_types or ["daily"]
    if types == ["daily"] or (len(types) == 1 and types[0] == "daily"):
        return f"z dnia <b>{day}</b>"

    parts: list[str] = []
    for t in types:
        label = LABELS.get(t, t)
        if t == "daily":
            parts.append(f"raport dzienny z {day}")
        elif t == "weekly":
            parts.append("<b>podsumowanie tygodnia</b>")
        elif t == "monthly":
            parts.append("<b>podsumowanie miesiąca</b>")
        elif t == "quarterly":
            parts.append("<b>podsumowanie kwartału</b>")
        elif t == "yearly":
            parts.append("<b>podsumowanie roku</b>")
        else:
            parts.append(label)
    if len(parts) == 1:
        return f"— {parts[0]} (stan na <b>{day}</b>)"
    joined = " oraz ".join(parts) if len(parts) == 2 else (", ".join(parts[:-1]) + " oraz " + parts[-1])
    return f"— {joined} (stan na <b>{day}</b>)"


def subject_line(report_types: list[str], report_date: datetime, market: dict[str, Any] | None = None) -> str:
    """Temat maila z datą / okresem."""
    day = report_date.strftime("%d.%m.%Y")
    types = report_types or ["daily"]
    market = market or {}

    def prices() -> str:
        from btc_report.render import fmt_num

        btc = fmt_num(market.get("btc_usd"), 0)
        eth = fmt_num(market.get("eth_usd"), 0)
        if market.get("btc_usd") is None and market.get("eth_usd") is None:
            return ""
        return f" — BTC {btc} · ETH {eth} USD"

    if types == ["daily"] or (len(types) == 1 and types[0] == "daily"):
        return f"Raport BTC/ETH z dnia {day}{prices()}"

    labels = []
    for t in types:
        if t == "daily":
            labels.append(f"dzień {day}")
        elif t == "weekly":
            labels.append("tydzień")
        elif t == "monthly":
            labels.append("miesiąc")
        elif t == "quarterly":
            labels.append("kwartał")
        elif t == "yearly":
            labels.append("rok")
        else:
            labels.append(t)
    period = " + ".join(labels)
    return f"Podsumowanie BTC/ETH — {period} (stan na {day}){prices()}"
