"""USDT exchange rates (USDT_USD, USDT_THB, USDT_KRW, USDT_ZAR, USDT_AUD, USDT_IDR)
from public crypto exchange APIs.

OANDA only has regular currencies, so RateService uses this for pairs starting with
USDT_. No API keys are needed. Each rate has a fallback exchange where one exists.
"""

import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from .fx_quote import Quote
from .top_of_book import clean_decimal

log = logging.getLogger(__name__)

Fetcher = Callable[[httpx.Client], tuple[Decimal, Decimal, datetime]]  # -> (bid, ask, time)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def coinbase_usdt_usd(http: httpx.Client) -> tuple[Decimal, Decimal, datetime]:
    data = _get(http, "https://api.coinbase.com/api/v3/brokerage/market/products/USDT-USD/ticker", limit=1)
    return clean_decimal(data["best_bid"]), clean_decimal(data["best_ask"]), _now()


def kraken_usdt_usd(http: httpx.Client) -> tuple[Decimal, Decimal, datetime]:
    data = _get(http, "https://api.kraken.com/0/public/Ticker", pair="USDTUSD")
    if data.get("error"):
        raise RuntimeError(f"Kraken error: {', '.join(data['error'])}")
    ticker = next(iter(data["result"].values()))  # keyed by Kraken's name, e.g. "USDTZUSD"
    return clean_decimal(ticker["b"][0]), clean_decimal(ticker["a"][0]), _now()


def bitkub_usdt_thb(http: httpx.Client) -> tuple[Decimal, Decimal, datetime]:
    data = _get(http, "https://api.bitkub.com/api/v3/market/ticker", sym="USDT_THB")[0]
    return clean_decimal(data["highest_bid"]), clean_decimal(data["lowest_ask"]), _now()


def upbit_usdt_krw(http: httpx.Client) -> tuple[Decimal, Decimal, datetime]:
    book = _get(http, "https://api.upbit.com/v1/orderbook", markets="KRW-USDT")[0]
    best = book["orderbook_units"][0]
    time = datetime.fromtimestamp(book["timestamp"] / 1000, tz=timezone.utc)
    return clean_decimal(str(best["bid_price"])), clean_decimal(str(best["ask_price"])), time


def bithumb_usdt_krw(http: httpx.Client) -> tuple[Decimal, Decimal, datetime]:
    data = _get(http, "https://api.bithumb.com/public/orderbook/USDT_KRW", count=1)["data"]
    time = datetime.fromtimestamp(int(data["timestamp"]) / 1000, tz=timezone.utc)
    return clean_decimal(data["bids"][0]["price"]), clean_decimal(data["asks"][0]["price"]), time


def valr_usdt_zar(http: httpx.Client) -> tuple[Decimal, Decimal, datetime]:
    data = _get(http, "https://api.valr.com/v1/public/USDTZAR/marketsummary")
    return clean_decimal(data["bidPrice"]), clean_decimal(data["askPrice"]), _now()


def luno_usdt_zar(http: httpx.Client) -> tuple[Decimal, Decimal, datetime]:
    data = _get(http, "https://api.luno.com/api/1/ticker", pair="USDTZAR")
    time = datetime.fromtimestamp(data["timestamp"] / 1000, tz=timezone.utc)
    return clean_decimal(data["bid"]), clean_decimal(data["ask"]), time


def btcmarkets_usdt_aud(http: httpx.Client) -> tuple[Decimal, Decimal, datetime]:
    data = _get(http, "https://api.btcmarkets.net/v3/markets/USDT-AUD/ticker")
    return clean_decimal(data["bestBid"]), clean_decimal(data["bestAsk"]), _now()


def independent_reserve_usdt_aud(http: httpx.Client) -> tuple[Decimal, Decimal, datetime]:
    data = _get(
        http, "https://api.independentreserve.com/Public/GetMarketSummary",
        primaryCurrencyCode="usdt", secondaryCurrencyCode="aud",
    )
    return clean_decimal(str(data["CurrentHighestBidPrice"])), clean_decimal(str(data["CurrentLowestOfferPrice"])), _now()


def indodax_usdt_idr(http: httpx.Client) -> tuple[Decimal, Decimal, datetime]:
    ticker = _get(http, "https://indodax.com/api/ticker/usdtidr")["ticker"]
    time = datetime.fromtimestamp(int(ticker["server_time"]), tz=timezone.utc)
    return clean_decimal(ticker["buy"]), clean_decimal(ticker["sell"]), time  # buy = best bid, sell = best ask


# Price of 1 USDT in each currency, from that country's own USDT market (so local
# premiums mostly cancel out when converting). The first exchange is tried first.
SOURCES: dict[str, list[Fetcher]] = {
    "USDT_USD": [coinbase_usdt_usd, kraken_usdt_usd],
    "USDT_THB": [bitkub_usdt_thb],
    "USDT_KRW": [upbit_usdt_krw, bithumb_usdt_krw],
    "USDT_ZAR": [valr_usdt_zar, luno_usdt_zar],
    "USDT_AUD": [btcmarkets_usdt_aud, independent_reserve_usdt_aud],
    "USDT_IDR": [indodax_usdt_idr],
}


def _get(http: httpx.Client, url: str, **params) -> dict | list:
    response = http.get(url, params=params)
    response.raise_for_status()
    return response.json()


class UsdtRateClient:
    """Fetches USDT rates. Same interface as OandaClient: get_quotes(pairs) and close()."""

    def __init__(self, client: httpx.Client = None, sources: dict[str, list[Fetcher]] = SOURCES) -> None:
        self._http = client or httpx.Client(timeout=10)
        self._sources = sources

    def get_quotes(self, pairs: Iterable[str]) -> list[Quote]:
        """Quotes for the pairs that have a source; unknown pairs are left out."""
        quotes = []
        for pair in pairs:
            fetchers = self._sources.get(pair)
            if not fetchers:
                log.warning("No USDT rate source for %s", pair)
                continue
            errors = []
            for fetch in fetchers:
                try:
                    bid, ask, time = fetch(self._http)
                except Exception as error:  # try the next exchange
                    errors.append(f"{fetch.__name__}: {error}")
                    continue
                quotes.append(Quote(pair, bid, ask, time))
                break
            else:
                raise RuntimeError(f"No {pair} rate available ({'; '.join(errors)})")
        return quotes

    def close(self) -> None:
        self._http.close()
