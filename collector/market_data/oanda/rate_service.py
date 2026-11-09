"""Exchange rates, cached for an hour: forex from the OANDA v20 REST API, and USDT
rates (USDT_USD, USDT_THB, USDT_KRW, USDT_ZAR, USDT_AUD, USDT_IDR) from public crypto exchange APIs.

Call it from any collector:

    from market_data.oanda.rate_service import RateService

    eur = RateService.get_quote("EUR_USD")     # Quote(pair, bid, ask, time)
    thb = RateService.get_quote("USDT_THB")    # baht for 1 USDT
    majors = RateService.get_quotes()          # {"EUR_USD": Quote, ...}

    # Prices from one currency to another (through USDT), e.g. a baht bid/ask in USDT:
    bid, ask = RateService.convert(bid_thb, ask_thb, "THB", "USDT")

The first call fetches rates; later calls return the cached rates until they are
an hour old, then the next call fetches fresh ones. Each source is only contacted
once one of its pairs is asked for, so USDT rates work without OANDA credentials.

Forex pairs need OANDA_API_TOKEN and OANDA_ACCOUNT_ID in collector/.env (see .env.example).
Run from the collector/ directory:

    python -m market_data.oanda.rate_service
    python -m market_data.oanda.rate_service EUR_USD USD_JPY USDT_THB USDT_KRW
"""

import argparse
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from decimal import Decimal
from typing import Protocol

import httpx
from dotenv import load_dotenv

from ..common.fx_quote import Quote
from ..common.usdt_rates import UsdtRateClient

log = logging.getLogger(__name__)

PRACTICE_URL = "https://api-fxpractice.oanda.com"
LIVE_URL = "https://api-fxtrade.oanda.com"

MAJOR_PAIRS = ("EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "AUD_USD", "USD_CAD", "NZD_USD")

FOREX = "forex"  # OANDA
USDT = "usdt"  # public crypto exchange APIs

BRIDGE = "USDT"  # conversions go through USDT: every USDT_<currency> rate links a currency to it


def source_for(pair: str) -> str:
    return USDT if pair.startswith("USDT_") else FOREX


def round_price(value: Decimal) -> Decimal:
    """Converted prices have long decimals; keep a sensible number of them."""
    step = Decimal("0.01") if value >= 100 else Decimal("0.0001") if value >= 1 else Decimal("0.00000001")
    rounded = value.quantize(step).normalize()
    # normalize() writes round numbers like 75000 as 7.5E+4; keep them as plain integers.
    return rounded.quantize(Decimal(1)) if rounded.as_tuple().exponent > 0 else rounded


class RateSource(Protocol):
    def get_quotes(self, pairs: Iterable[str]) -> list[Quote]: ...

    def close(self) -> None: ...


def parse_time(value: str) -> datetime:
    """Parse OANDA's RFC 3339 time, which has nanoseconds, keeping microseconds."""
    value = re.sub(r"(\.\d{6})\d+", r"\1", value)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_prices(payload: dict) -> list[Quote]:
    """Turn a /pricing response into Quotes, using the best bid and ask."""
    quotes = []
    for price in payload.get("prices", []):
        if not price.get("bids") or not price.get("asks"):
            continue  # e.g. market closed with no prices
        quotes.append(
            Quote(
                pair=price["instrument"],
                bid=Decimal(price["bids"][0]["price"]),
                ask=Decimal(price["asks"][0]["price"]),
                time=parse_time(price["time"]),
            )
        )
    return quotes


class OandaClient:
    """Makes the HTTP request to OANDA. Use RateService instead of calling this directly."""

    def __init__(
        self,
        token: str,
        account_id: str,
        *,
        live: bool = False,
        client: httpx.Client = None,
    ) -> None:
        self.account_id = account_id
        self._client = client or httpx.Client(
            base_url=LIVE_URL if live else PRACTICE_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )

    @classmethod
    def from_env(cls) -> "OandaClient":
        """Build from OANDA_API_TOKEN, OANDA_ACCOUNT_ID and OANDA_ENV (practice|live)."""
        load_dotenv()
        token = os.getenv("OANDA_API_TOKEN")
        account_id = os.getenv("OANDA_ACCOUNT_ID")
        if not token or not account_id:
            raise RuntimeError("Set OANDA_API_TOKEN and OANDA_ACCOUNT_ID in collector/.env")
        live = os.getenv("OANDA_ENV", "practice").lower() == "live"
        return cls(token, account_id, live=live)

    def get_quotes(self, pairs: Iterable[str]) -> list[Quote]:
        """Return the current bid, ask and time for each pair, e.g. ["EUR_USD"]."""
        response = self._client.get(
            f"/v3/accounts/{self.account_id}/pricing",
            params={"instruments": ",".join(pairs)},
        )
        if response.is_error:
            message = response.json().get("errorMessage", response.text) if response.content else ""
            if response.status_code == 401:
                server = "live" if str(self._client.base_url).rstrip("/") == LIVE_URL else "practice"
                message += (
                    f" (sent to the {server} server: check the token, and that OANDA_ENV matches"
                    " the account type the token was created for)"
                )
            raise RuntimeError(f"OANDA error {response.status_code}: {message}")
        return parse_prices(response.json())

    def close(self) -> None:
        self._client.close()


class RateService:
    """Shared, cached exchange rates. All methods are class methods; don't instantiate it.

    Rates for every pair requested so far are fetched together (one OANDA request for
    all forex pairs; the majors come along with the first one). Safe to call from several threads.
    """

    REFRESH_SECONDS = 60 * 60  # refetch rates once they're an hour old
    RETRY_SECONDS = 60  # if a refresh fails, keep the old rates and retry after this

    _clients: dict[str, RateSource] = {}  # source -> client, created on first use
    _quotes: dict[str, Quote] = {}
    _tracked: dict[str, set[str]] = {FOREX: set(), USDT: set()}  # pairs refreshed each hour
    _next_refresh: float = 0.0  # time.monotonic() value; 0 = fetch on first call
    _lock = threading.Lock()
    _clock: Callable[[], float] = time.monotonic

    def __new__(cls, *args, **kwargs):
        raise TypeError("RateService is static; call RateService.get_quote(...) directly")

    @classmethod
    def get_quote(cls, pair: str) -> Quote:
        """Return the cached quote for one pair, e.g. "EUR_USD"."""
        return cls.get_quotes([pair])[pair]

    @classmethod
    def get_quotes(cls, pairs: Iterable[str] = None) -> dict[str, Quote]:
        """Return cached quotes keyed by pair (default: the majors)."""
        wanted = list(dict.fromkeys(pairs if pairs is not None else MAJOR_PAIRS))  # dedupe, keep order
        with cls._lock:
            new_pairs = {p for p in wanted if p not in cls._tracked[source_for(p)]}
            if new_pairs or cls._clock() >= cls._next_refresh:
                cls._refresh(new_pairs)
            missing = set(wanted) - cls._quotes.keys()
            if missing:
                raise KeyError(f"No rate available for {', '.join(sorted(missing))}")
            return {pair: cls._quotes[pair] for pair in wanted}

    @staticmethod
    def conversion_pairs(source: str, target: str) -> list[str]:
        """Rates needed to convert prices from one currency to another, e.g. THB -> USDT: ["USDT_THB"]."""
        if source == target:
            return []
        return [f"USDT_{currency}" for currency in (source, target) if currency != BRIDGE]

    @classmethod
    def can_convert(cls, source: str, target: str) -> bool:
        """Whether rates exist to convert between these currencies (fetches them if needed)."""
        try:
            cls.get_quotes(cls.conversion_pairs(source, target))
        except KeyError:
            return False
        return True

    @classmethod
    def convert(cls, bid: Decimal, ask: Decimal, source: str, target: str) -> tuple[Decimal, Decimal]:
        """Convert a bid/ask from one currency to another, going through USDT.

        E.g. a BTC bid/ask in THB -> in USDT, or KRW -> USD (KRW -> USDT -> USD).
        Conservative: the bid uses the side of each rate that gives less, the ask the side
        that costs more, so a converted price is never better than what you could get.
        Raises KeyError if a rate isn't available. May make HTTP requests when rates are
        due a refresh, so call it with asyncio.to_thread from async code.
        """
        if source == target:
            return bid, ask
        rates = cls.get_quotes(cls.conversion_pairs(source, target))
        if source != BRIDGE:  # e.g. THB -> USDT: divide by baht per USDT
            rate = rates[f"USDT_{source}"]
            bid, ask = bid / rate.ask, ask / rate.bid
        if target != BRIDGE:  # e.g. USDT -> USD: multiply by dollars per USDT
            rate = rates[f"USDT_{target}"]
            bid, ask = bid * rate.bid, ask * rate.ask
        return round_price(bid), round_price(ask)

    @classmethod
    def refresh(cls) -> None:
        """Fetch fresh rates now, without waiting for the hour to pass."""
        with cls._lock:
            cls._refresh(set())

    @classmethod
    def configure(cls, client: RateSource, source: str = FOREX) -> None:
        """Use a specific client for a source (e.g. in tests) and clear the cache."""
        with cls._lock:
            cls._clients[source] = client
            cls._clear()

    @classmethod
    def reset(cls) -> None:
        """Clear the cache and clients; the next call fetches again."""
        with cls._lock:
            for client in cls._clients.values():
                client.close()
            cls._clients = {}
            cls._clear()

    @classmethod
    def _clear(cls) -> None:
        cls._quotes = {}
        cls._tracked = {FOREX: set(), USDT: set()}
        cls._next_refresh = 0.0

    @classmethod
    def _client_for(cls, source: str) -> RateSource:
        if source not in cls._clients:
            cls._clients[source] = OandaClient.from_env() if source == FOREX else UsdtRateClient()
        return cls._clients[source]

    @classmethod
    def _refresh(cls, new_pairs: set[str]) -> None:
        """Fetch tracked pairs plus new ones from each source in use. Caller must hold the lock."""
        wanted = {source: set(pairs) for source, pairs in cls._tracked.items()}
        for pair in new_pairs:
            wanted[source_for(pair)].add(pair)
        if wanted[FOREX]:
            wanted[FOREX] |= set(MAJOR_PAIRS)  # same request, so the majors are always on hand

        failed, error = False, None
        for source, pairs in wanted.items():
            if not pairs:
                continue  # never asked for: don't contact it (e.g. no OANDA credentials needed)
            try:
                quotes = cls._client_for(source).get_quotes(sorted(pairs))
            except Exception as failure:
                failed = True
                if pairs & cls._quotes.keys():
                    log.warning("%s rate refresh failed; using cached rates", source, exc_info=True)
                else:
                    error = error or failure  # nothing cached to fall back on
                continue
            cls._tracked[source] = pairs  # only track new pairs once the source has accepted them
            cls._quotes.update({q.pair: q for q in quotes})
        cls._next_refresh = cls._clock() + (cls.RETRY_SECONDS if failed else cls.REFRESH_SECONDS)
        if error:
            raise error

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pairs", nargs="*", default=MAJOR_PAIRS, help="Pairs like EUR_USD (default: majors)")
    args = parser.parse_args()

    for q in RateService.get_quotes(args.pairs).values():
        print(f"{q.pair:<8} bid {q.bid:<10} ask {q.ask:<10} {q.time:%Y-%m-%d %H:%M:%S.%f} UTC")


if __name__ == "__main__":
    main()
