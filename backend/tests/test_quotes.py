import asyncio
import json

from fastapi.testclient import TestClient

from app import quotes
from app.main import app
from app.quotes import QuoteBroadcaster, event_stream

SNAPSHOT = {"symbol": "BTC-USDT", "exchanges": [], "best_bid": None, "best_ask": None, "spread": None, "crossed": False}


def parse(message: str) -> dict:
    assert message.startswith("data: ") and message.endswith("\n\n")
    return json.loads(message.removeprefix("data: "))


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


def test_slow_subscriber_drops_oldest(monkeypatch) -> None:
    monkeypatch.setattr(quotes, "QUEUE_SIZE", 2)
    source = QuoteBroadcaster(min_interval=0)  # no rate limit, to fill the queue
    queue = source.subscribe()
    for spread in ("1", "2", "3"):
        source.publish({**SNAPSHOT, "spread": spread})
    assert [parse(queue.get_nowait())["spread"] for _ in range(2)] == ["2", "3"]


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
