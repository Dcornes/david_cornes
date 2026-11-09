from decimal import Decimal

from market_data.coinone.order_book_stream import parse_orderbook

# Real (trimmed) Coinone data: asks arrive highest first.
DATA = {
    "quote_currency": "KRW", "target_currency": "BTC", "timestamp": 1794261975151,
    "asks": [{"price": "101920000", "qty": "0.00032381"}, {"price": "101910000", "qty": "0.00032384"}],
    "bids": [{"price": "101850000", "qty": "0.1"}, {"price": "101860000", "qty": "0.02"}],
}


def test_parse_orderbook_finds_best_whatever_the_order() -> None:
    base, top, time = parse_orderbook(DATA)
    assert base == "BTC"
    assert top == (Decimal("101860000"), Decimal("0.02"), Decimal("101910000"), Decimal("0.00032384"))
    assert time.year == 2026
