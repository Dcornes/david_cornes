from decimal import Decimal

from market_data.indodax.order_book_stream import parse_depth

# A real (trimmed) /api/depth/btcidr response: buy = bids, sell = asks.
DEPTH = {"buy": [["1355000000", "0.00407561"], ["1354516000", "0.39709035"]], "sell": [["1356572000", "0.66328350"], ["1356573000", "0.05848854"]]}


def test_parse_depth() -> None:
    assert parse_depth(DEPTH) == (Decimal("1355000000"), Decimal("0.00407561"), Decimal("1356572000"), Decimal("0.6632835"))
