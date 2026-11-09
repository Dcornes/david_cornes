import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from market_data.common.top_of_book import TopOfBook
from market_data.market_data_handler import MarketDataHandler, MarketSnapshot

D = Decimal
NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)


def quote(exchange, bid, ask, symbol=("BTC", "USDT"), bid_qty="1", ask_qty="1") -> TopOfBook:
    return TopOfBook(exchange, symbol[0], symbol[1], NOW, D(bid), D(bid_qty), D(ask), D(ask_qty))


def handler(**kwargs) -> tuple[MarketDataHandler, list[MarketSnapshot]]:
    shown: list[MarketSnapshot] = []
    return MarketDataHandler(["BTC-USDT"], streams={}, on_update=shown.append, **kwargs), shown


def test_best_bid_and_ask_come_from_different_exchanges() -> None:
    h, _ = handler()
    h.on_quote(quote("OKX", "100", "102"))
    h.on_quote(quote("Kraken", "101", "103"))
    h.on_quote(quote("Binance.US", "99", "101.5"))

    best = h.best("BTC-USDT")
    assert (best.bid_price, best.bid_exchange) == (D("101"), "Kraken")
    assert (best.ask_price, best.ask_exchange) == (D("101.5"), "Binance.US")
    assert best.spread == D("0.5")
    assert not best.is_crossed


def test_newer_quote_replaces_same_exchange() -> None:
    h, _ = handler()
    h.on_quote(quote("OKX", "105", "106"))
    h.on_quote(quote("OKX", "100", "101"))
    assert h.best("BTC-USDT").bid_price == D("100")


def test_pairs_are_tracked_separately_and_lookup_accepts_any_format() -> None:
    h, _ = handler()
    h.on_quote(quote("OKX", "100", "101"))
    h.on_quote(quote("OKX", "5", "6", symbol=("ETH", "USDT")))
    assert h.best("btc/usdt").bid_price == D("100")
    assert h.best("ETH_USDT").bid_price == D("5")
    assert h.best("SOL-USDT") is None


def test_price_tie_prefers_larger_quantity() -> None:
    h, _ = handler()
    h.on_quote(quote("OKX", "100", "101", bid_qty="1", ask_qty="1"))
    h.on_quote(quote("Kraken", "100", "101", bid_qty="5", ask_qty="7"))
    best = h.best("BTC-USDT")
    assert (best.bid_exchange, best.ask_exchange) == ("Kraken", "Kraken")


def test_crossed_market_is_flagged() -> None:
    h, _ = handler()
    h.on_quote(quote("OKX", "100", "101"))
    h.on_quote(quote("Kraken", "102", "103"))
    best = h.best("BTC-USDT")
    assert best.is_crossed
    assert str(best).endswith("CROSSED")


def test_sends_snapshot_whenever_any_exchange_price_changes() -> None:
    h, sent = handler()
    h.on_quote(quote("OKX", "100", "101"))  # sent
    h.on_quote(quote("OKX", "100", "101", bid_qty="9"))  # quantity only: not sent
    h.on_quote(quote("Kraken", "99", "102"))  # new exchange: sent, even though not best
    h.on_quote(quote("Kraken", "99.5", "102"))  # Kraken price changed: sent
    h.on_quote(quote("Kraken", "100.5", "102"))  # new best bid: sent

    assert [sorted(s.quotes) for s in sent] == [["OKX"], ["Kraken", "OKX"], ["Kraken", "OKX"], ["Kraken", "OKX"]]
    assert [s.quotes["Kraken"].bid_price for s in sent[1:]] == [D("99"), D("99.5"), D("100.5")]
    assert (sent[-1].best.bid_price, sent[-1].best.bid_exchange) == (D("100.5"), "Kraken")


def test_snapshot_to_dict() -> None:
    h, _ = handler()
    h.on_quote(quote("OKX", "100", "102"))
    h.on_quote(quote("Kraken", "101", "103", bid_qty="0.5"))

    assert h.snapshot("BTC-USDT").to_dict() == {
        "symbol": "BTC-USDT",
        "exchanges": [
            {"exchange": "Kraken", "bid": "101", "bid_quantity": "0.5", "ask": "103", "ask_quantity": "1",
             "timestamp": "2026-11-09T00:00:00+00:00"},
            {"exchange": "OKX", "bid": "100", "bid_quantity": "1", "ask": "102", "ask_quantity": "1",
             "timestamp": "2026-11-09T00:00:00+00:00"},
        ],
        "best_bid": {"price": "101", "quantity": "0.5", "exchange": "Kraken"},
        "best_ask": {"price": "102", "quantity": "1", "exchange": "OKX"},
        "spread": "1",
        "crossed": False,
    }


def test_removing_last_exchange_sends_empty_snapshot() -> None:
    h, sent = handler()
    h.on_quote(quote("OKX", "100", "101"))
    h.remove_exchange("OKX")

    assert sent[-1].best is None
    assert sent[-1].to_dict()["exchanges"] == []
    assert sent[-1].to_dict()["crossed"] is False


def test_str_shows_prices_and_exchanges() -> None:
    h, _ = handler()
    h.on_quote(quote("OKX", "100", "102"))
    h.on_quote(quote("Kraken", "101", "103"))
    assert str(h.best("BTC-USDT")) == (
        "BTC-USDT   bid 101 x 1 (Kraken)  |  ask 102 x 1 (OKX)  spread 1"
    )


def test_run_merges_streams_and_survives_a_failing_exchange() -> None:
    async def okx(*symbols):
        assert symbols == ("BTC-USDT",)
        yield quote("OKX", "100", "102")
        await asyncio.sleep(0.01)  # let kraken report and fail before this ends
        yield quote("OKX", "100", "101.5")

    async def kraken(*symbols):
        yield quote("Kraken", "101", "103")
        raise RuntimeError("Kraken error: Currency pair not supported")

    sent: list[MarketSnapshot] = []
    h = MarketDataHandler(["BTC-USDT"], streams={"okx": okx, "kraken": kraken}, on_update=sent.append)
    asyncio.run(h.run())

    # Kraken's 101 bid was best until its feed failed, then its quotes were dropped.
    assert any(s.best and s.best.bid_exchange == "Kraken" for s in sent)
    last_with_prices = [s for s in sent if s.best][-1]
    assert list(last_with_prices.quotes) == ["OKX"]
    assert last_with_prices.best.ask_price == D("101.5")
    # okx finished normally too, so everything is cleared at the end
    assert h.best("BTC-USDT") is None
    assert sent[-1].best is None


def test_reconnects_a_stopped_feed(monkeypatch) -> None:
    attempts = []

    async def flaky(*symbols):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("connection dropped")
        yield quote("OKX", "100", "101")
        raise asyncio.CancelledError  # end the test once it has reconnected

    monkeypatch.setattr(MarketDataHandler, "RECONNECT_MIN_SECONDS", 0)
    sent: list[MarketSnapshot] = []
    h = MarketDataHandler(["BTC-USDT"], streams={"okx": flaky}, on_update=sent.append, reconnect=True)
    try:
        asyncio.run(h.run())
    except asyncio.CancelledError:
        pass

    assert len(attempts) == 3
    assert sent[0].best.bid_price == D("100")


def test_feed_status_follows_connect_fail_and_reconnect(monkeypatch) -> None:
    attempts = []

    async def flaky(*symbols):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("connection dropped")
        yield quote("OKX", "100", "101")
        raise asyncio.CancelledError  # end the test once it's live again

    monkeypatch.setattr(MarketDataHandler, "RECONNECT_MIN_SECONDS", 0)
    changes: list[list] = []
    h = MarketDataHandler(
        ["BTC-USDT"], streams={"okx": flaky}, on_update=lambda s: None, reconnect=True, on_status=changes.append
    )
    assert [(s.exchange, s.state) for s in h.statuses()] == [("OKX", "connecting")]  # display name, not "okx"
    try:
        asyncio.run(h.run())
    except asyncio.CancelledError:
        pass

    assert [(c[0].state, c[0].message) for c in changes] == [
        ("reconnecting", "connection dropped"),
        ("live", ""),  # reported once, not on every quote
    ]


def test_feed_status_stopped_without_reconnect() -> None:
    async def ends(*symbols):
        yield quote("Kraken", "100", "101")

    changes: list[list] = []
    h = MarketDataHandler(["BTC-USDT"], streams={"kraken": ends}, on_update=lambda s: None, on_status=changes.append)
    asyncio.run(h.run())

    assert [c[0].state for c in changes] == ["live", "stopped"]
    assert changes[-1][0].to_dict()["message"] == "Feed ended"
