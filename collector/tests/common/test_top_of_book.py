from datetime import datetime, timezone
from decimal import Decimal

import pytest

from market_data.common.top_of_book import TopOfBook, split_symbol


def quote(instrument_1: str, instrument_2: str) -> TopOfBook:
    return TopOfBook(
        exchange="Kraken",
        instrument_1=instrument_1,
        instrument_2=instrument_2,
        timestamp=datetime(2026, 11, 9, tzinfo=timezone.utc),
        bid_price=Decimal("1"),
        bid_quantity=Decimal("1"),
        ask_price=Decimal("2"),
        ask_quantity=Decimal("1"),
    )


def test_symbol_is_built_from_instruments() -> None:
    q = quote("BTC", "USD")
    assert (q.symbol, q.instrument_1, q.instrument_2) == ("BTC-USD", "BTC", "USD")


def test_xbt_is_converted_to_btc() -> None:
    q = quote("XBT", "USD")
    assert (q.symbol, q.instrument_1) == ("BTC-USD", "BTC")


def test_codes_are_upper_cased() -> None:
    assert quote("eth", "xbt").symbol == "ETH-BTC"


@pytest.mark.parametrize("symbol", ["BTC-USD", "BTC/USD", "BTC_USD", "XBT/USD", "xbt-usd"])
def test_split_symbol(symbol: str) -> None:
    assert split_symbol(symbol) == ("BTC", "USD")


@pytest.mark.parametrize("symbol", ["BTCUSD", "BTC-USDT-SWAP", "BTC-"])
def test_split_symbol_rejects_non_pairs(symbol: str) -> None:
    with pytest.raises(ValueError):
        split_symbol(symbol)


def test_converted_from_is_optional_and_shown_when_set() -> None:
    from datetime import datetime, timezone
    from decimal import Decimal

    from market_data.common.top_of_book import TopOfBook

    args = ("Bitkub", "BTC", "USDT", datetime(2026, 11, 9, tzinfo=timezone.utc), Decimal(1), Decimal(1), Decimal(2), Decimal(1))
    assert TopOfBook(*args).converted_from is None
    converted = TopOfBook(*args, converted_from="THB")
    assert converted.converted_from == "THB"
    assert str(converted).endswith("(converted from THB)")
