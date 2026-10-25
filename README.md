# btc-daily-report

Codzienne (i okresowe) raporty BTC na e-mail — źródła darmowe, wysyłka przez Resend, automatyzacja GitHub Actions (działa także gdy komputer jest wyłączony).

## Co jest w raporcie

- Cena BTC w USD / EUR / PLN + kursy NBP (USD/PLN, EUR/PLN)
- Zmiany 24h / 7d / 30d, Fear & Greed, volume, market cap, dominacja, hashrate
- Napływy ETF (XOOMAR + fallback Farside)
- Netflow „dużych ryb” (CryptoDataAPI) + opcjonalnie Whale Alert + duże TX z mempool.space
- Makro: RSS ekonomiczne + opcjonalnie FRED (10Y, Fed funds)
- Newsy krypto PL/EN + BitcoinTalk
- Wnioski AI (Groq)
- Wykres świecowy (obraz) + link TradingView
- Każde generowanie zapisuje też `output/report_latest.html` (zawsze najnowszy)

## Szybki start (Linux)

```bash
cd ~/btc-daily-report
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# uzupełnij .env
python -m btc_report daily          # lokalny HTML w output/
python -m btc_report daily --send   # + mail
python -m btc_report auto --send    # daily + weekly/monthly… jeśli wypada
```

## Wymagane konta (darmowe / tanie)

| Sekret | Po co |
|--------|--------|
| `RESEND_API_KEY` | wysyłka maila |
| `REPORT_TO_EMAIL` | Twój interia.pl |
| `REPORT_FROM_EMAIL` | np. `beth.t@example.com` na start |
| `GROQ_API_KEY` | wnioski AI przez Groq (preferowane; darmowy tier) |
| `OPENROUTER_API_KEY` | alternatywa zamiast Groq |
| `CRYPTODATAAPI_KEY` | netflow giełdowy BTC (free tier) |
| `WHALE_ALERT_API_KEY` | opcjonalnie duże transfery z etykietami (free: ~1h, min ~500k USD) |
| `FRED_API_KEY` | opcjonalnie stopy / 10Y |
| `COINGECKO_API_KEY` | opcjonalnie wyższe limity |

## GitHub Actions (mail bez włączonego PC)

Codziennie ~06:45 Europe/Warsaw czasu zimowego (cron `45 5 * * *` UTC; latem ~07:45) workflow odpala `python -m btc_report auto --send`.

1. Załóż [Resend](https://resend.com) → API Keys → skopiuj `re_…`. Na start `REPORT_FROM_EMAIL=beth.t@example.com`.
2. Do lokalnego `.env` dodaj: `RESEND_API_KEY`, `REPORT_TO_EMAIL` (np. interia), `REPORT_FROM_EMAIL`.
3. Test lokalny: `python -m btc_report daily --send` (musi dojść mail).
4. Wrzuć projekt na **private** GitHub (wymaga `git`).
5. Settings → Secrets and variables → Actions — dodaj co najmniej:
   - `RESEND_API_KEY`, `REPORT_TO_EMAIL`, `REPORT_FROM_EMAIL`
   - `GROQ_API_KEY`, `AI_MODEL`, `AI_MODEL_NEWS`, `AI_BASE_URL` (`https://api.groq.com/openai/v1`)
   - `CRYPTODATAAPI_KEY`
6. Actions → **BTC daily report** → **Run workflow** (pierwszy test ręczny).

## Konfiguracja źródeł

Edytuj `config.yaml` (RSS PL/EN/makro, limity, TradingView URL).

## Uwagi

- Interaktywne wskaźniki TA są na TradingView (link w mailu) — klienci poczty nie uruchamiają JS.
- BitcoinTalk / Farside bywają kruche (HTML się zmienia lub blokuje boty) — wtedy sekcja będzie pusta, reszta raportu dalej działa.
- To narzędzie badawcze, nie doradztwo inwestycyjne.
