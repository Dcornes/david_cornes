from datetime import datetime, timezone
from decimal import Decimal

from market_data.valr.order_book_stream import parse_orderbook

MESSAGE = {
    "type": "AGGREGATED_ORDERBOOK_UPDATE", "currencyPairSymbol": "BTCZAR",
    "data": {
        "Asks": [{"side": "sell", "quantity": "0.00066106", "price": "1242851", "currencyPair": "BTCZAR", "orderCount": 1},
                 {"side": "sell", "quantity": "0.4837545", "price": "1243447", "currencyPair": "BTCZAR", "orderCount": 2}],
        "Bids": [{"side": "buy", "quantity": "0.02087728", "price": "1242709", "currencyPair": "BTCZAR", "orderCount": 1}],
        "LastChange": "2026-11-09T22:07:00.423Z", "SequenceNumber": 1,
    },
}


def test_parse_orderbook() -> None:
    pair, top, time = parse_orderbook(MESSAGE)
    assert pair == "BTCZAR"
    assert top == (Decimal("1242709"), Decimal("0.02087728"), Decimal("1242851"), Decimal("0.00066106"))
    assert time == datetime(2026, 11, 9, 22, 7, 0, 423000, tzinfo=timezone.utc)
