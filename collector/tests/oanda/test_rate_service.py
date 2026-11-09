from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from market_data.oanda.rate_service import OandaClient, Quote, RateService, parse_prices, parse_time

SAMPLE = {
    "time": "2026-11-09T14:30:00.123456789Z",
    "prices": [
        {
            "instrument": "EUR_USD",
            "time": "2026-11-09T14:29:59.987654321Z",
            "bids": [{"price": "1.08512", "liquidity": 10000000}],
            "asks": [{"price": "1.08526", "liquidity": 10000000}],
        },
        {
            "instrument": "USD_JPY",
            "time": "2026-11-09T14:29:58.000000000Z",
            "bids": [{"price": "151.203", "liquidity": 10000000}],
            "asks": [{"price": "151.218", "liquidity": 10000000}],
        },
    ],
}

TIME = datetime(2026, 11, 9, tzinfo=timezone.utc)


class FakeClient:
    """Stands in for OandaClient: counts requests and returns a price per pair."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.bid = Decimal("1.0")
        self.fail = False

    def get_quotes(self, pairs):
        pairs = list(pairs)
        self.calls.append(pairs)
        if self.fail:
            raise RuntimeError("OANDA error 503")
        return [Quote(p, self.bid, self.bid + Decimal("0.0001"), TIME) for p in pairs]

    def close(self) -> None:
        pass


@pytest.fixture
def fake(monkeypatch):
    """Point RateService at a FakeClient and a clock the test controls."""
    now = {"t": 1000.0}
    monkeypatch.setattr(RateService, "_clock", staticmethod(lambda: now["t"]))
    client = FakeClient()
    RateService.configure(client)
    client.now = now
    yield client
    RateService.reset()


# --- OandaClient / parsing -------------------------------------------------


def test_parse_time_keeps_microseconds() -> None:
    assert parse_time("2026-11-09T14:29:59.987654321Z") == datetime(
        2026, 11, 9, 14, 29, 59, 987654, tzinfo=timezone.utc
    )


def test_parse_prices() -> None:
    eur, jpy = parse_prices(SAMPLE)
    assert (eur.pair, eur.bid, eur.ask) == ("EUR_USD", Decimal("1.08512"), Decimal("1.08526"))
    assert eur.spread == Decimal("0.00014")
    assert jpy.pair == "USD_JPY"


def test_parse_prices_skips_pairs_without_prices() -> None:
    payload = {"prices": [{"instrument": "EUR_USD", "time": "2026-11-09T00:00:00Z", "bids": [], "asks": []}]}
    assert parse_prices(payload) == []


def oanda_client(handler) -> OandaClient:
    http = httpx.Client(base_url="https://test", transport=httpx.MockTransport(handler))
    return OandaClient("token", "001-001-1234567-001", client=http)


def test_client_sends_pairs_to_pricing_endpoint() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["instruments"] = request.url.params["instruments"]
        return httpx.Response(200, json=SAMPLE)

    quotes = oanda_client(handler).get_quotes(["EUR_USD", "USD_JPY"])

    assert seen == {"path": "/v3/accounts/001-001-1234567-001/pricing", "instruments": "EUR_USD,USD_JPY"}
    assert [q.pair for q in quotes] == ["EUR_USD", "USD_JPY"]


def test_client_raises_oanda_error_message() -> None:
    client = oanda_client(lambda r: httpx.Response(401, json={"errorMessage": "Insufficient authorization"}))
    with pytest.raises(RuntimeError, match="401: Insufficient authorization"):
        client.get_quotes(["EUR_USD"])


def test_from_env_requires_credentials(monkeypatch) -> None:
    monkeypatch.setattr("market_data.oanda.rate_service.load_dotenv", lambda: None)
    monkeypatch.delenv("OANDA_API_TOKEN", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)
    with pytest.raises(RuntimeError, match="OANDA_API_TOKEN"):
        OandaClient.from_env()


# --- RateService caching ---------------------------------------------------


def test_cannot_instantiate() -> None:
    with pytest.raises(TypeError):
        RateService()


def test_first_call_fetches_then_uses_cache(fake) -> None:
    assert RateService.get_quote("EUR_USD").bid == Decimal("1.0")
    RateService.get_quote("USD_JPY")
    RateService.get_quotes()

    assert len(fake.calls) == 1  # one request covered all majors


def test_refetches_after_an_hour(fake) -> None:
    RateService.get_quote("EUR_USD")
    fake.bid = Decimal("2.0")

    fake.now["t"] += RateService.REFRESH_SECONDS - 1
    assert RateService.get_quote("EUR_USD").bid == Decimal("1.0")  # still cached

    fake.now["t"] += 1
    assert RateService.get_quote("EUR_USD").bid == Decimal("2.0")  # an hour old: refetched
    assert len(fake.calls) == 2


def test_new_pair_is_fetched_and_then_tracked(fake) -> None:
    RateService.get_quotes()
    assert RateService.get_quote("EUR_JPY").pair == "EUR_JPY"
    assert "EUR_JPY" in fake.calls[-1] and "EUR_USD" in fake.calls[-1]

    fake.now["t"] += RateService.REFRESH_SECONDS
    RateService.get_quotes()
    assert "EUR_JPY" in fake.calls[-1]  # included in hourly refreshes from now on


def test_failed_refresh_keeps_old_rates_and_retries_soon(fake) -> None:
    RateService.get_quote("EUR_USD")
    fake.fail = True
    fake.now["t"] += RateService.REFRESH_SECONDS

    assert RateService.get_quote("EUR_USD").bid == Decimal("1.0")  # stale but usable
    RateService.get_quote("EUR_USD")
    assert len(fake.calls) == 2  # didn't retry on every call

    fake.fail = False
    fake.now["t"] += RateService.RETRY_SECONDS
    RateService.get_quote("EUR_USD")
    assert len(fake.calls) == 3


def test_failure_with_empty_cache_raises(fake) -> None:
    fake.fail = True
    with pytest.raises(RuntimeError, match="503"):
        RateService.get_quote("EUR_USD")


def test_usdt_pairs_come_from_the_usdt_source_without_contacting_oanda(fake) -> None:
    usdt = FakeClient()
    RateService._clients["usdt"] = usdt

    assert RateService.get_quote("USDT_THB").pair == "USDT_THB"
    assert usdt.calls == [["USDT_THB"]]
    assert fake.calls == []  # no OANDA request (works without OANDA credentials)

    RateService.get_quote("EUR_USD")
    assert "EUR_USD" in fake.calls[-1] and "USDT_THB" not in fake.calls[-1]


def test_usdt_failure_doesnt_block_cached_forex(fake) -> None:
    usdt = FakeClient()
    RateService._clients["usdt"] = usdt
    RateService.get_quotes(["EUR_USD", "USDT_THB"])

    usdt.fail = True
    fake.now["t"] += RateService.REFRESH_SECONDS
    assert RateService.get_quote("EUR_USD").bid == Decimal("1.0")  # forex refreshed fine
    assert RateService.get_quote("USDT_THB").bid == Decimal("1.0")  # stale USDT rate still served


def test_rejected_new_pair_is_not_tracked(fake) -> None:
    RateService.get_quotes()
    fake.fail = True
    with pytest.raises(KeyError, match="BAD_PAIR"):
        RateService.get_quote("BAD_PAIR")

    fake.fail = False
    fake.now["t"] += RateService.REFRESH_SECONDS
    RateService.get_quotes()
    assert "BAD_PAIR" not in fake.calls[-1]
