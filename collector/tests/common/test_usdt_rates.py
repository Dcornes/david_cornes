from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from market_data.common.usdt_rates import (
    UsdtRateClient,
    bithumb_usdt_krw,
    bitkub_usdt_thb,
    coinbase_usdt_usd,
    kraken_usdt_usd,
    upbit_usdt_krw,
)

# Trimmed real responses.
RESPONSES = {
    "api.coinbase.com": {"trades": [], "best_bid": "0.99875", "best_ask": "0.99876"},
    "api.kraken.com": {"error": [], "result": {"USDTZUSD": {"a": ["0.99880", "1", "1"], "b": ["0.99870", "1", "1"]}}},
    "api.bitkub.com": [{"symbol": "USDT_THB", "highest_bid": "33.39", "lowest_ask": "33.4"}],
    "api.upbit.com": [{"market": "KRW-USDT", "timestamp": 1794258534925, "orderbook_units": [{"bid_price": 1354, "ask_price": 1355.0}]}],
    "api.bithumb.com": {"status": "0000", "data": {"timestamp": "1794258537778", "bids": [{"price": "1354"}], "asks": [{"price": "1355"}]}},
}


def http(fail: set[str] = frozenset()) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host in fail:
            return httpx.Response(503, json={"error": ["EService:Unavailable"]})
        return httpx.Response(200, json=RESPONSES[request.url.host])

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "fetch, bid, ask",
    [
        (coinbase_usdt_usd, "0.99875", "0.99876"),
        (kraken_usdt_usd, "0.9987", "0.9988"),
        (bitkub_usdt_thb, "33.39", "33.4"),
        (upbit_usdt_krw, "1354", "1355"),
        (bithumb_usdt_krw, "1354", "1355"),
    ],
)
def test_each_exchange_parses_best_bid_and_ask(fetch, bid, ask) -> None:
    got_bid, got_ask, time = fetch(http())
    assert (got_bid, got_ask) == (Decimal(bid), Decimal(ask))
    assert time.tzinfo == timezone.utc


def test_upbit_uses_exchange_time() -> None:
    assert upbit_usdt_krw(http())[2] == datetime(2026, 11, 9, 21, 8, 54, 925000, tzinfo=timezone.utc)


def test_client_returns_quotes_for_known_pairs() -> None:
    quotes = UsdtRateClient(http()).get_quotes(["USDT_USD", "USDT_THB", "USDT_KRW", "USDT_EUR"])
    assert [(q.pair, q.bid) for q in quotes] == [
        ("USDT_USD", Decimal("0.99875")),
        ("USDT_THB", Decimal("33.39")),
        ("USDT_KRW", Decimal("1354")),
    ]  # USDT_EUR has no source, so it's left out


def test_client_falls_back_to_the_next_exchange() -> None:
    (quote,) = UsdtRateClient(http(fail={"api.coinbase.com"})).get_quotes(["USDT_USD"])
    assert quote.bid == Decimal("0.9987")  # from Kraken


def test_client_raises_when_every_exchange_fails() -> None:
    with pytest.raises(RuntimeError, match="No USDT_USD rate"):
        UsdtRateClient(http(fail={"api.coinbase.com", "api.kraken.com"})).get_quotes(["USDT_USD"])
