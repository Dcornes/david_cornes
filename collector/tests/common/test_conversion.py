import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from market_data.common import conversion
from market_data.common.conversion import Target, convert, round_price, targets_for
from market_data.common.fx_quote import Quote

TIME = datetime(2026, 11, 9, tzinfo=timezone.utc)
RATES = {
    "USDT_THB": Quote("USDT_THB", Decimal("33.39"), Decimal("33.40"), TIME),
    "USDT_KRW": Quote("USDT_KRW", Decimal("1354"), Decimal("1355"), TIME),
    "USDT_USD": Quote("USDT_USD", Decimal("0.99875"), Decimal("0.99876"), TIME),
}
BID, ASK = Decimal("2499774.37"), Decimal("2500735.36")  # BTC in THB (real Bitkub prices)


def test_rate_pairs_needed() -> None:
    assert Target("BTC", "USDT", "THB").rate_pairs == ["USDT_THB"]
    assert Target("BTC", "USDT", "KRW").rate_pairs == ["USDT_KRW"]
    assert Target("BTC", "USD", "THB").rate_pairs == ["USDT_THB", "USDT_USD"]
    assert Target("BTC", "THB", "THB").rate_pairs == []


def test_converted_from_only_when_a_rate_is_used() -> None:
    assert Target("BTC", "USDT", "KRW").converted_from == "KRW"
    assert Target("BTC", "KRW", "KRW").converted_from is None


def test_convert_to_usdt_is_conservative() -> None:
    bid, ask = convert(BID, ASK, Target("BTC", "USDT", "THB"), RATES)
    assert bid == round_price(BID / Decimal("33.40"))  # bid divided by the higher rate: less USDT
    assert ask == round_price(ASK / Decimal("33.39"))  # ask divided by the lower rate: costs more
    assert (bid, ask) == (Decimal("74843.54"), Decimal("74894.74"))


def test_convert_krw_to_usdt() -> None:
    bid, ask = convert(Decimal("101484000"), Decimal("101485000"), Target("BTC", "USDT", "KRW"), RATES)
    assert (bid, ask) == (Decimal("74895.94"), Decimal("74951.99"))  # 101484000 / 1355, 101485000 / 1354


def test_convert_to_usd_goes_through_usdt() -> None:
    bid, ask = convert(BID, ASK, Target("BTC", "USD", "THB"), RATES)
    assert bid == round_price(BID / Decimal("33.40") * Decimal("0.99875"))
    assert ask == round_price(ASK / Decimal("33.39") * Decimal("0.99876"))


def test_local_currency_is_unchanged() -> None:
    assert convert(BID, ASK, Target("BTC", "THB", "THB"), RATES) == (BID, ASK)


@pytest.mark.parametrize(
    "value, rounded",
    [("74843.5412", "74843.54"), ("12.345678", "12.3457"), ("0.0123456789", "0.01234568"), ("100.10", "100.1")],
)
def test_round_price(value: str, rounded: str) -> None:
    assert round_price(Decimal(value)) == Decimal(rounded)


def test_targets_skip_unlisted_and_unconvertible(monkeypatch) -> None:
    def get_quotes(pairs):
        if any(p not in RATES for p in pairs):
            raise KeyError("No rate available")
        return {p: RATES[p] for p in pairs}

    monkeypatch.setattr(conversion.RateService, "get_quotes", get_quotes)
    listed = {"BTC", "ETH"}

    targets = asyncio.run(targets_for("Bitkub", "THB", ("BTC-USDT", "ETH-USDT", "SOL-USDT", "BTC-EUR"), listed))
    assert targets == [Target("BTC", "USDT", "THB"), Target("ETH", "USDT", "THB")]  # no SOL_THB market; no EUR rate

    with pytest.raises(RuntimeError, match="Bitkub can't provide SOL-USDT"):
        asyncio.run(targets_for("Bitkub", "THB", ("SOL-USDT",), listed))
