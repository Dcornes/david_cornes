# david_cornes

FastAPI backend + Angular frontend, plus separate packages the backend uses for market data, snow reports and lift stats.

Pages: `/` résumé (home), `/markets` live crypto quotes, `/snow` ski area snow totals and webcams, `/lifts` lift vertical vs ride time.

```
backend/     website API (FastAPI, Python 3.12)
frontend/    website UI (Angular 22, Node 22+)
collector/   market-data code (OKX, Kraken, Binance.US, Crypto.com, Bitstamp, Coinbase, Gemini, Bitfinex, Bitkub, Upbit, Bithumb, KuCoin, Gate, MEXC, Korbit, Coinone, VALR, BTC Markets, Indodax, OANDA); market_data/ is also used by the backend
snow/        snow totals and webcams for Bridger Bowl, Big Sky, Jackson Hole, Grand Targhee, Revelstoke and Palisades Tahoe (snow_report package)
lifts/       lift vertical rise vs ride time from Lift Blog's sheets, plus your real lap times from Strava (lift_vertical package)
```

## Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
fastapi dev app/main.py
```

`requirements-dev.txt` also installs `../collector` (the `market_data` package), `../snow` (`snow_report`) and `../lifts` (`lift_vertical`) as editable packages.

API runs at http://localhost:8000 — interactive docs at http://localhost:8000/docs.

On startup the backend streams OKX, Kraken, Binance.US, Crypto.com, Bitstamp, Coinbase, Gemini, Bitfinex, Bitkub, Upbit, Bithumb, KuCoin, Gate, MEXC, Korbit, Coinone, VALR, BTC Markets and Indodax quotes and serves them to the `/markets` page:

- `GET /api/quotes` returns the latest snapshot for every pair.
- `GET /api/quotes/stream` is a Server-Sent Events stream: a full snapshot of each pair, then `update` events with only what changed, at most 1 per second per pair (faster changes are combined, ending on the newest price). `status` events carry each exchange feed's health.
- `GET /api/quotes/status` returns each exchange feed's health (connecting, live, reconnecting or stopped).
- `QUOTE_SYMBOLS` sets the pairs (default `BTC-USDT,ETH-USDT`).

It also fetches snow totals hourly and lift data daily:

- `GET /api/snow` returns each resort's summary stats, webcams and detail tables (`SNOW_ENABLED=0` turns fetching off). Sources: Bridger Bowl's public GraphQL API, Big Sky's snow depth and webcams pages, Jackson Hole's mountain report JSON, Revelstoke's snow report and webcams pages, and the USDA SNOTEL stations on Grand Targhee and Palisades Tahoe (Targhee's site blocks automated requests, so its webcams are linked rather than shown; Palisades' own report needs a third-party key, so only its webcams come from its site).
- `GET /api/lifts` returns every operating lift's vertical rise, ride time and vertical feet per minute (`LIFTS_ENABLED=0` turns fetching off).
- `GET /api/lifts/mine` returns per-lift medians of your real rides and laps from Strava (ride, ski-down, line and round-trip times). Only totals are published, never GPS tracks or dates. It needs `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` and `STRAVA_REFRESH_TOKEN` (in `backend/.env` locally, see `backend/.env.example`; in Render's Environment settings for the live site). Get a refresh token with activity access once with `python -m lift_vertical.strava_connect` from `lifts/`, which saves it to `backend/.env`. Lift locations come from OpenStreetMap. Analysed ski days are kept in Postgres when `DATABASE_URL` is set (a Neon connection string works), so a deploy doesn't download every day from Strava again; without it they're kept in `backend/.cache/my_laps.json`.
- `GET /api/lifts/heatmap?resort=Big Sky&min_snow=8` powers the Snow heatmap page (`/heatmap`): where you skied on days with at least `min_snow` inches of new snow in the 48 hours before, as a heat grid summed over those days plus per-run descent counts (no dates or single tracks). New snow comes from Bridger Bowl's morning snow reports, or SNOTEL (Lone Mountain for Big Sky). `GET /api/lifts/heatmap/runs` returns the resort's runs (from the shipped `lifts/lift_vertical/data/pistes.json`; refresh with `python -m lift_vertical.runs --snapshot`). Days' runs and heat grids are kept in the `ski_days` table and daily snow in `snow_days`.

Both packages run on their own too, from their folders: `python -m snow_report.resorts`, `python -m lift_vertical.liftblog` and `python -m lift_vertical.my_laps`. Their tests run with `pytest` from `snow/` and `lifts/`.
- `MARKET_DATA_ENABLED=0` turns it off.

Run tests:

```bash
pytest
```

## Frontend

```bash
cd frontend
npm install
npm start
```

App runs at http://localhost:4200; `/api/*` requests are proxied to the backend on port 8000 (see `frontend/proxy.conf.json`). Start the backend first.

Run tests / build:

```bash
npm test
npm run build
```

## Collector

Standalone Python package for gathering market data. It has its own dependencies and is not included in the website's Docker image; changes here don't redeploy the site (see `buildFilter` in `render.yaml`).

```bash
cd collector
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

API keys go in `collector/.env`, which is git-ignored. Start from the template:

```bash
copy .env.example .env
```

Then fill in your real values.

Stream live order books (public data, no keys needed); Ctrl+C to stop:

```bash
python -m market_data.okx.order_book_stream BTC-USDT ETH-USDT --channel books5
python -m market_data.kraken.order_book_stream BTC-USD ETH-USD
python -m market_data.binance.order_book_stream BTC-USDT ETH-USD
python -m market_data.cryptocom.order_book_stream BTC-USDT ETH-USD
python -m market_data.bitstamp.order_book_stream BTC-USD ETH-USDT
python -m market_data.coinbase.order_book_stream BTC-USD ETH-USDT
python -m market_data.gemini.order_book_stream BTC-USD ETH-USDT
python -m market_data.bitfinex.order_book_stream BTC-USDT ETH-USD
python -m market_data.bitkub.order_book_stream BTC-USDT ETH-USDT
python -m market_data.upbit.order_book_stream BTC-USDT ETH-USDT
python -m market_data.bithumb.order_book_stream BTC-USDT ETH-USDT
python -m market_data.kucoin.order_book_stream BTC-USDT ETH-USDT
python -m market_data.gate.order_book_stream BTC-USDT ETH-USDT
python -m market_data.mexc.order_book_stream BTC-USDT ETH-USDT
python -m market_data.korbit.order_book_stream BTC-USDT ETH-USDT
python -m market_data.coinone.order_book_stream BTC-USDT ETH-USDT
python -m market_data.valr.order_book_stream BTC-USDT ETH-USDT
python -m market_data.btcmarkets.order_book_stream BTC-USDT ETH-USDT
python -m market_data.indodax.order_book_stream BTC-USDT ETH-USDT
```

Bitkub (Thai baht), Upbit, Bithumb, Korbit and Coinone (Korean won), VALR (South African rand), BTC Markets (Australian dollar) and Indodax (Indonesian rupiah) price their markets in a local currency; their clients convert to the pair you ask for (USDT here) with `RateService.convert`, which goes through USDT using each country's own USDT market as the rate (USDT/THB, USDT/KRW, USDT/ZAR, USDT/AUD, USDT/IDR). The markets page marks converted prices with an exchange-rate icon.

Best bid and ask across the active exchanges (Binance.US, Kraken and Crypto.com; set `ACTIVE_EXCHANGES` in `market_data_handler.py` to change them), with the exchange each came from. `--exchanges` picks any of the nineteen:

```bash
python -m market_data.market_data_handler BTC-USDT ETH-USDT
python -m market_data.market_data_handler BTC-USDT --exchanges okx coinbase upbit
```

Get exchange rates, cached for an hour: forex from OANDA (needs `OANDA_API_TOKEN` and `OANDA_ACCOUNT_ID` in `.env`; defaults to the major pairs), and USDT_USD, USDT_THB, USDT_KRW, USDT_ZAR, USDT_AUD and USDT_IDR from public crypto exchange APIs (no keys needed):

```bash
python -m market_data.oanda.rate_service
python -m market_data.oanda.rate_service EUR_USD GBP_JPY
python -m market_data.oanda.rate_service USDT_USD USDT_THB USDT_KRW
```

Run tests:

```bash
pip install -r requirements-dev.txt
pytest
```
