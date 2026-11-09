import asyncio

import pytest

from market_data.common import local_markets
from market_data.common.local_markets import Target, targets_for


def test_converted_from_only_when_a_rate_is_used() -> None:
    assert Target("BTC", "USDT", "KRW").converted_from == "KRW"
    assert Target("BTC", "KRW", "KRW").converted_from is None


def test_targets_skip_unlisted_and_unconvertible(monkeypatch) -> None:
    monkeypatch.setattr(local_markets.RateService, "can_convert", lambda source, target: target in ("USDT", source))
    listed = {"BTC", "ETH"}

    targets = asyncio.run(targets_for("Bitkub", "THB", ("BTC-USDT", "ETH-USDT", "SOL-USDT", "BTC-EUR", "BTC-THB"), listed))
    # No SOL_THB market; no THB -> EUR rate. BTC-THB needs no rate at all.
    assert targets == [Target("BTC", "USDT", "THB"), Target("ETH", "USDT", "THB"), Target("BTC", "THB", "THB")]

    with pytest.raises(RuntimeError, match="Bitkub can't provide SOL-USDT"):
        asyncio.run(targets_for("Bitkub", "THB", ("SOL-USDT",), listed))


def test_best_levels_ignores_order_and_empty_levels() -> None:
    from decimal import Decimal

    from market_data.common.local_markets import best_levels

    # Asks listed highest first (like Coinone), and an empty best bid level (like Bithumb's rounding).
    bids = [("99", "0"), ("98", "2.50"), ("97", "1")]
    asks = [("103", "1"), ("102", "0.5"), ("101", "0.25000000")]
    assert best_levels(bids, asks) == (Decimal("98"), Decimal("2.5"), Decimal("101"), Decimal("0.25"))
    assert best_levels([], asks) is None


def test_converted_quotes_converts_each_target_once_per_change(monkeypatch) -> None:
    from datetime import datetime, timezone
    from decimal import Decimal

    from market_data.common.local_markets import ConvertedQuotes

    monkeypatch.setattr(local_markets.RateService, "convert", lambda bid, ask, source, target: (bid / 2, ask / 2))
    targets = [Target("BTC", "USDT", "ZAR"), Target("BTC", "ZAR", "ZAR"), Target("ETH", "USDT", "ZAR")]
    converter = ConvertedQuotes("VALR", targets, lambda t: f"{t.base}ZAR")
    time = datetime(2026, 11, 9, tzinfo=timezone.utc)
    top = (Decimal("100"), Decimal("1"), Decimal("102"), Decimal("2"))

    assert converter.markets == ["BTCZAR", "ETHZAR"]
    quotes = asyncio.run(converter.quotes("BTCZAR", top, time))
    assert [(q.symbol, q.bid_price, q.ask_price, q.converted_from) for q in quotes] == [
        ("BTC-USDT", Decimal("50"), Decimal("51"), "ZAR"),
        ("BTC-ZAR", Decimal("50"), Decimal("51"), None),  # (fake convert halves everything)
    ]
    assert asyncio.run(converter.quotes("BTCZAR", top, time)) == []  # unchanged
    assert asyncio.run(converter.quotes("XRPZAR", top, time)) == []  # not requested
