from datetime import datetime, timezone
from decimal import Decimal

import pytest

from market_data.coinbase.order_book_stream import check_subscriptions, parse_ticker, parse_time, to_coinbase_symbol

# A real ticker (Coinbase also sends last price and 24h/52w stats).
TICKER = {
    "type": "ticker", "product_id": "BTC-USDT", "price": "74868.73", "volume_24_h": "30.57027652",
    "best_bid": "74865.67", "best_ask": "74906.74", "best_bid_quantity": "0.00221067", "best_ask_quantity": "0.00080943",
}
TIME = datetime(2026, 11, 9, 19, 59, 21, 743088, tzinfo=timezone.utc)


def test_to_coinbase_symbol() -> None:
    assert to_coinbase_symbol("BTC/USD") == "BTC-USD"
    assert to_coinbase_symbol("xbt_usdt") == "BTC-USDT"


def test_parse_time_drops_nanoseconds() -> None:
    assert parse_time("2026-11-09T19:59:21.743088571Z") == TIME
    assert parse_time("2026-11-09T19:59:21Z") == TIME.replace(microsecond=0)


def test_parse_ticker() -> None:
    quote = parse_ticker(TICKER, TIME)

    assert (quote.exchange, quote.symbol) == ("Coinbase", "BTC-USDT")
    assert (quote.bid_price, quote.bid_quantity) == (Decimal("74865.67"), Decimal("0.00221067"))
    assert (quote.ask_price, quote.ask_quantity) == (Decimal("74906.74"), Decimal("0.00080943"))
    assert quote.timestamp == TIME


def test_check_subscriptions() -> None:
    check_subscriptions(["BTC-USD", "NOPE-USD"], ["BTC-USD"])  # one missing: just a warning
    with pytest.raises(RuntimeError, match="no product NOPE-USD"):
        check_subscriptions(["NOPE-USD"], [])
