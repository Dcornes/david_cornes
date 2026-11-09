# david_cornes

FastAPI backend + Angular frontend, plus a separate market-data collector.

```
backend/     website API (FastAPI, Python 3.12)
frontend/    website UI (Angular 22, Node 22+)
collector/   market-data code (OKX, Kraken, Binance.US, Crypto.com, Bitstamp, Coinbase, Gemini, Bitfinex, OANDA); market_data/ is also used by the backend
```

## Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
fastapi dev app/main.py
```

`requirements-dev.txt` also installs `../collector` (the `market_data` package) so the backend can stream quotes.

API runs at http://localhost:8000 — interactive docs at http://localhost:8000/docs.

On startup the backend streams OKX, Kraken, Binance.US, Crypto.com, Bitstamp, Coinbase, Gemini and Bitfinex quotes and serves them to the `/markets` page:

- `GET /api/quotes` returns the latest snapshot for every pair.
- `GET /api/quotes/stream` is a Server-Sent Events stream: a full snapshot of each pair, then `update` events with only what changed, at most 1 per second per pair (faster changes are combined, ending on the newest price). `status` events carry each exchange feed's health.
- `GET /api/quotes/status` returns each exchange feed's health (connecting, live, reconnecting or stopped).
- `QUOTE_SYMBOLS` sets the pairs (default `BTC-USDT,ETH-USDT`).
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
```

Best bid and ask across all eight exchanges, with the exchange each came from:

```bash
python -m market_data.market_data_handler BTC-USDT ETH-USDT
```

Get forex bid/ask quotes from OANDA (needs `OANDA_API_TOKEN` and `OANDA_ACCOUNT_ID` in `.env`; defaults to the major pairs):

```bash
python -m market_data.oanda.rate_service
python -m market_data.oanda.rate_service EUR_USD GBP_JPY
```

Run tests:

```bash
pip install -r requirements-dev.txt
pytest
```
