import asyncio
import json

from fastapi.testclient import TestClient

from app import quotes
from app.main import app
from app.quotes import QuoteBroadcaster, event_stream

SNAPSHOT = {"symbol": "BTC-USDT", "exchanges": [], "best_bid": None, "best_ask": None, "spread": None, "crossed": False}


def parse(message: str) -> dict:
    """A full snapshot or an `update` event (both have symbol, best prices, spread and crossed)."""
    message = message.removeprefix("event: update\n")
    assert message.startswith("data: ") and message.endswith("\n\n")
    return json.loads(message.removeprefix("data: "))


def quote(exchange: str, bid: str, ask: str, qty: str = "1") -> dict:
    return {"exchange": exchange, "bid": bid, "bid_quantity": qty, "ask": ask, "ask_quantity": qty, "timestamp": "t"}


def test_stream_sends_current_snapshots_then_new_ones() -> None:
    async def scenario() -> list[str]:
        source = QuoteBroadcaster()
        source.publish(SNAPSHOT)
        stream = event_stream(source)
        first = await anext(stream)  # current snapshot on connect
        source.publish({**SNAPSHOT, "spread": "1"})
        second = await anext(stream)  # then the new one
        assert source.subscriber_count == 1
        await stream.aclose()  # browser disconnects
        assert source.subscriber_count == 0
        return [first, second]

    first, second = asyncio.run(scenario())
    assert parse(first) == SNAPSHOT
    assert parse(second)["spread"] == "1"


def test_stream_sends_heartbeat_when_quiet() -> None:
    async def scenario() -> str:
        stream = event_stream(QuoteBroadcaster(), heartbeat=0.01)
        try:
            return await anext(stream)
        finally:
            await stream.aclose()

    assert asyncio.run(scenario()) == ": heartbeat\n\n"


def test_slow_subscriber_gets_full_snapshots_instead_of_a_gap(monkeypatch) -> None:
    monkeypatch.setattr(quotes, "QUEUE_SIZE", 3)
    source = QuoteBroadcaster(min_interval=0)  # no rate limit, to fill the queue
    source.publish_status([{"exchange": "OKX", "state": "live"}])
    queue = source.subscribe()
    for spread in ("1", "2", "3", "4"):
        source.publish({**SNAPSHOT, "spread": spread})

    backlog = [queue.get_nowait() for _ in range(queue.qsize())]
    assert len(backlog) == 2
    assert backlog[0].startswith("event: status")
    assert backlog[1] == quotes.sse({**SNAPSHOT, "spread": "4"})  # full snapshot of the newest state


def test_first_send_is_full_then_only_changes() -> None:
    source = QuoteBroadcaster(min_interval=0)
    queue = source.subscribe()
    first = {**SNAPSHOT, "exchanges": [quote("Kraken", "1", "3"), quote("OKX", "2", "4")]}
    source.publish(first)
    source.publish({**first, "exchanges": [quote("Kraken", "1", "3"), quote("OKX", "2.5", "4")], "spread": "0.5"})
    source.publish({**first, "exchanges": [quote("OKX", "2.5", "4")]})

    full, update, removal = (queue.get_nowait() for _ in range(3))
    assert full == quotes.sse(first)
    assert update.startswith("event: update\n")
    assert parse(update)["changed"] == [quote("OKX", "2.5", "4")]  # Kraken didn't change, so isn't sent
    assert parse(update)["spread"] == "0.5"
    assert (parse(removal)["changed"], parse(removal)["removed"]) == ([], ["Kraken"])


def test_quantity_only_changes_are_not_sent() -> None:
    source = QuoteBroadcaster(min_interval=0)
    queue = source.subscribe()
    first = {**SNAPSHOT, "exchanges": [quote("Kraken", "1", "3"), quote("OKX", "2", "4")]}
    source.publish(first)
    queue.get_nowait()

    # Kraken's quantity changes but not its price; OKX's price changes, with a new quantity.
    source.publish({**first, "exchanges": [quote("Kraken", "1", "3", qty="9"), quote("OKX", "2.5", "4", qty="7")]})
    assert parse(queue.get_nowait())["changed"] == [quote("OKX", "2.5", "4", qty="7")]

    # Only quantities change: nothing is sent at all.
    source.publish({**first, "exchanges": [quote("Kraken", "1", "3", qty="5"), quote("OKX", "2.5", "4", qty="5")]})
    assert queue.empty()

    # What new browsers start from matches what existing ones have: Kraken's quantity is the old one.
    assert source.sent["BTC-USDT"]["exchanges"] == [quote("Kraken", "1", "3"), quote("OKX", "2.5", "4", qty="7")]
    assert source.latest["BTC-USDT"]["exchanges"][0]["bid_quantity"] == "5"  # /api/quotes still has the newest


def test_new_browser_starts_from_what_was_last_sent() -> None:
    async def scenario() -> dict:
        source = QuoteBroadcaster(min_interval=60)
        source.publish(SNAPSHOT)
        source.publish({**SNAPSHOT, "spread": "2"})  # held back; the next update will be relative to SNAPSHOT
        stream = event_stream(source)
        try:
            return parse(await anext(stream))
        finally:
            await stream.aclose()

    assert asyncio.run(scenario()) == SNAPSHOT


def test_rate_limit_sends_first_now_and_newest_after_interval() -> None:
    async def scenario() -> tuple[list[str], list[str], list[str]]:
        source = QuoteBroadcaster(min_interval=0.05)
        queue = source.subscribe()
        for spread in ("1", "2", "3", "4"):  # four changes in quick succession
            source.publish({**SNAPSHOT, "spread": spread})
        right_away = [parse(queue.get_nowait())["spread"] for _ in range(queue.qsize())]
        await asyncio.sleep(0.1)
        after_interval = [parse(queue.get_nowait())["spread"] for _ in range(queue.qsize())]
        source.publish({**SNAPSHOT, "symbol": "ETH-USDT", "spread": "9"})  # other pairs have their own limit
        other_pair = [parse(queue.get_nowait())["symbol"] for _ in range(queue.qsize())]
        return right_away, after_interval, other_pair

    right_away, after_interval, other_pair = asyncio.run(scenario())
    assert right_away == ["1"]  # first change goes out immediately
    assert after_interval == ["4"]  # 2 and 3 are skipped; the newest is sent once the interval is up
    assert other_pair == ["ETH-USDT"]


def test_latest_is_always_newest_even_before_it_is_sent() -> None:
    async def scenario() -> dict:
        source = QuoteBroadcaster(min_interval=60)
        source.publish(SNAPSHOT)
        source.publish({**SNAPSHOT, "spread": "2"})  # held back by the rate limit
        return source.latest["BTC-USDT"]

    assert asyncio.run(scenario())["spread"] == "2"  # new page loads and /api/quotes still get it


def test_message_is_built_once_for_all_subscribers(monkeypatch) -> None:
    calls = []
    real_sse = quotes.sse
    monkeypatch.setattr(quotes, "sse", lambda data: calls.append(1) or real_sse(data))

    source = QuoteBroadcaster(min_interval=0)
    queues = [source.subscribe() for _ in range(5)]
    source.publish(SNAPSHOT)

    messages = [q.get_nowait() for q in queues]
    assert len(calls) == 1
    assert all(m is messages[0] for m in messages)  # the very same string object


def test_latest_quotes_endpoint(monkeypatch) -> None:
    source = QuoteBroadcaster()
    source.publish(SNAPSHOT)
    monkeypatch.setattr(quotes, "broadcaster", source)
    assert TestClient(app).get("/api/quotes").json() == [SNAPSHOT]


def test_spa_fallback_serves_index_but_not_for_api(tmp_path) -> None:
    from fastapi import FastAPI

    from app.main import SPAStaticFiles

    (tmp_path / "index.html").write_text("<app-root></app-root>")
    site = FastAPI()
    site.mount("/", SPAStaticFiles(directory=tmp_path, html=True))
    client = TestClient(site)

    assert "<app-root>" in client.get("/markets").text
    assert client.get("/api/nope").status_code == 404


def test_configured_symbols(monkeypatch) -> None:
    monkeypatch.setenv("QUOTE_SYMBOLS", " BTC-USDT , eth-usdt,")
    assert quotes.configured_symbols() == ["BTC-USDT", "eth-usdt"]


FEEDS = [{"exchange": "OKX", "state": "live", "message": "", "since": "2026-11-09T19:00:00+00:00"}]


def parse_status(message: str) -> list:
    assert message.startswith("event: status\ndata: ") and message.endswith("\n\n")
    return json.loads(message.removeprefix("event: status\ndata: "))


def test_stream_sends_feed_status_first_and_on_change() -> None:
    async def scenario() -> list[str]:
        source = QuoteBroadcaster()
        source.publish_status(FEEDS)
        source.publish(SNAPSHOT)
        stream = event_stream(source)
        on_connect = [await anext(stream), await anext(stream)]
        source.publish_status([{**FEEDS[0], "state": "reconnecting", "message": "boom"}])
        change = await anext(stream)
        await stream.aclose()
        return [*on_connect, change]

    status, snapshot, change = asyncio.run(scenario())
    assert parse_status(status) == FEEDS
    assert parse(snapshot) == SNAPSHOT
    assert parse_status(change)[0]["state"] == "reconnecting"


def test_status_is_not_rate_limited() -> None:
    source = QuoteBroadcaster(min_interval=60)
    queue = source.subscribe()
    for state in ("connecting", "live", "reconnecting"):
        source.publish_status([{**FEEDS[0], "state": state}])
    assert [parse_status(queue.get_nowait())[0]["state"] for _ in range(3)] == ["connecting", "live", "reconnecting"]


def test_feed_status_endpoint(monkeypatch) -> None:
    source = QuoteBroadcaster()
    source.publish_status(FEEDS)
    monkeypatch.setattr(quotes, "broadcaster", source)
    assert TestClient(app).get("/api/quotes/status").json() == FEEDS
