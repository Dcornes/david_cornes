import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from market_data.mexc import order_book_stream
from market_data.mexc.order_book_stream import decode_fields, fetch_pairs, parse_book_ticker, read_varint, to_mexc_symbol

# A real protobuf push: BTCUSDT best bid 75017.24 x 0.349832, ask 75017.25 x 0.0010664.
MESSAGE = bytes.fromhex(
    "0a3473706f74407075626c69632e61676772652e626f6f6b5469636b65722e76332e6170692e7062403130306d7340425443555344541a07"
    "4254435553445430c997d2929c34da133d0a0837353031372e32341208302e3334393833321a0837353031372e32352209302e3030313036"
    "36342a0b3835393232373034353338309997d2929c34"
)
PAIRS = {"BTCUSDT": ("BTC", "USDT")}


def test_to_mexc_symbol() -> None:
    assert to_mexc_symbol("BTC-USDT") == "BTCUSDT"
    assert to_mexc_symbol("xbt/usdt") == "BTCUSDT"


def test_read_varint() -> None:
    assert read_varint(bytes([0x96, 0x01]), 0) == (150, 2)  # the protobuf docs' example
    assert read_varint(bytes([0x08]), 0) == (8, 1)


def test_decode_fields() -> None:
    fields = decode_fields(MESSAGE)
    assert fields[1] == b"spot@public.aggre.bookTicker.v3.api.pb@100ms@BTCUSDT"
    assert fields[3] == b"BTCUSDT"
    assert isinstance(fields[6], int)  # send time
    assert 315 in fields  # the nested best bid/ask message


def test_parse_book_ticker() -> None:
    quote = parse_book_ticker(MESSAGE, PAIRS)

    assert (quote.exchange, quote.symbol) == ("MEXC", "BTC-USDT")
    assert (quote.bid_price, quote.bid_quantity) == (Decimal("75017.24"), Decimal("0.349832"))
    assert (quote.ask_price, quote.ask_quantity) == (Decimal("75017.25"), Decimal("0.0010664"))
    assert quote.timestamp.tzinfo == timezone.utc and quote.timestamp.year == 2026


def test_parse_book_ticker_ignores_other_symbols() -> None:
    assert parse_book_ticker(MESSAGE, {"ETHUSDT": ("ETH", "USDT")}) is None


def test_fetch_pairs_skips_unknown(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        symbol = request.url.params["symbol"]
        found = [{"symbol": symbol, "baseAsset": "BTC", "quoteAsset": "USDT", "status": "1"}] if symbol == "BTCUSDT" else []
        return httpx.Response(200, json={"symbols": found})  # MEXC answers unknown symbols with an empty list

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        order_book_stream.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert asyncio.run(fetch_pairs(["BTCUSDT", "NOPEUSDT"], "https://test")) == PAIRS
