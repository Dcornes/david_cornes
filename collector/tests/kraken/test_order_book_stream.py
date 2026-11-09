from decimal import Decimal

from market_data.kraken.order_book_stream import LocalBook, to_kraken_symbol, to_top_of_book

D = Decimal

SNAPSHOT = {
    "symbol": "BTC/USD",
    "bids": [{"price": D("74984.0"), "qty": D("1.5")}, {"price": D("74983.3"), "qty": D("0.2")}],
    "asks": [{"price": D("74984.1"), "qty": D("0.01")}, {"price": D("74985.0"), "qty": D("2")}],
    "timestamp": "2026-11-09T16:23:11.152443Z",
}


def test_to_kraken_symbol() -> None:
    assert to_kraken_symbol("BTC-USD") == "BTC/USD"
    assert to_kraken_symbol("XBT-USD") == "BTC/USD"  # Kraken v2 rejects XBT/USD
    assert to_kraken_symbol("eth_usd") == "ETH/USD"


def test_snapshot_gives_best_bid_and_ask() -> None:
    book = LocalBook()
    book.apply(SNAPSHOT, snapshot=True)
    assert book.best_bid() == (D("74984.0"), D("1.5"))
    assert book.best_ask() == (D("74984.1"), D("0.01"))


def test_update_removes_zero_quantity_and_adds_levels() -> None:
    book = LocalBook()
    book.apply(SNAPSHOT, snapshot=True)
    book.apply(
        {"bids": [{"price": D("74984.0"), "qty": D("0")}], "asks": [{"price": D("74984.05"), "qty": D("3")}]},
        snapshot=False,
    )
    assert book.best_bid() == (D("74983.3"), D("0.2"))  # old best bid removed
    assert book.best_ask() == (D("74984.05"), D("3"))  # new better ask


def test_new_snapshot_replaces_book() -> None:
    book = LocalBook()
    book.apply(SNAPSHOT, snapshot=True)
    book.apply({"bids": [{"price": D("1"), "qty": D("1")}], "asks": [{"price": D("2"), "qty": D("1")}]}, snapshot=True)
    assert book.best_bid() == (D("1"), D("1"))


def test_book_is_trimmed_to_depth() -> None:
    book = LocalBook(depth=2)
    book.apply(
        {
            "bids": [{"price": D(p), "qty": D("1")} for p in ("10", "9", "8")],
            "asks": [{"price": D(p), "qty": D("1")} for p in ("11", "12", "13")],
        },
        snapshot=True,
    )
    assert sorted(book.bids) == [D("9"), D("10")]
    assert sorted(book.asks) == [D("11"), D("12")]


def test_to_top_of_book_uses_okx_symbol_format() -> None:
    book = LocalBook()
    book.apply(SNAPSHOT, snapshot=True)
    quote = to_top_of_book("BTC/USD", book, SNAPSHOT["timestamp"])

    assert (quote.exchange, quote.symbol, quote.instrument_1, quote.instrument_2) == ("Kraken", "BTC-USD", "BTC", "USD")
    assert (quote.bid_price, quote.bid_quantity) == (D("74984.0"), D("1.5"))
    assert (quote.ask_price, quote.ask_quantity) == (D("74984.1"), D("0.01"))
    assert quote.timestamp.microsecond == 152443


def test_to_top_of_book_empty_side_returns_none() -> None:
    book = LocalBook()
    book.apply({"bids": [], "asks": [{"price": D("1"), "qty": D("1")}]}, snapshot=True)
    assert to_top_of_book("BTC/USD", book, SNAPSHOT["timestamp"]) is None
