from datetime import datetime, timezone
from decimal import Decimal

from market_data.cryptocom.order_book_stream import heartbeat_reply, parse_ticker, to_cryptocom_symbol

# A real ticker entry (trimmed of nothing; Crypto.com sends 24h stats alongside the top of book).
TICKER = {
    "h": "75953.98", "l": "73469.49", "a": "74679.45", "c": "-0.0159",
    "b": "74678.03", "bs": "0.02013", "k": "74678.04", "ks": "0.13120",
    "i": "BTC_USDT", "v": "2013.4407", "vv": "150399772.95", "oi": "0", "t": 1794252331676,
}


def test_to_cryptocom_symbol() -> None:
    assert to_cryptocom_symbol("BTC-USDT") == "BTC_USDT"
    assert to_cryptocom_symbol("xbt/usd") == "BTC_USD"


def test_parse_ticker() -> None:
    quote = parse_ticker(TICKER)

    assert (quote.exchange, quote.symbol, quote.instrument_1, quote.instrument_2) == ("Crypto.com", "BTC-USDT", "BTC", "USDT")
    assert (quote.bid_price, quote.bid_quantity) == (Decimal("74678.03"), Decimal("0.02013"))
    assert (quote.ask_price, quote.ask_quantity) == (Decimal("74678.04"), Decimal("0.1312"))
    assert quote.timestamp == datetime(2026, 11, 9, 19, 25, 31, 676000, tzinfo=timezone.utc)
    assert "Crypto.com" in str(quote)


def test_heartbeat_reply() -> None:
    heartbeat = {"id": 1794252430595, "method": "public/heartbeat", "code": 0}
    assert heartbeat_reply(heartbeat) == {"id": 1794252430595, "method": "public/respond-heartbeat"}
    assert heartbeat_reply({"id": 1, "method": "subscribe", "code": 0}) is None
