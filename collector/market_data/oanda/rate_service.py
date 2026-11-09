"""Forex bid/ask quotes from the OANDA v20 REST API, cached for an hour.

Call it from any collector:

    from market_data.oanda.rate_service import RateService

    eur = RateService.get_quote("EUR_USD")     # Quote(pair, bid, ask, time)
    majors = RateService.get_quotes()          # {"EUR_USD": Quote, ...}

The first call fetches rates from OANDA; later calls return the cached rates
until they are an hour old, then the next call fetches fresh ones.

Needs OANDA_API_TOKEN and OANDA_ACCOUNT_ID in collector/.env (see .env.example).
Run from the collector/ directory:

    python -m market_data.oanda.rate_service
    python -m market_data.oanda.rate_service EUR_USD USD_JPY
"""

import argparse
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import httpx
from dotenv import load_dotenv

log = logging.getLogger(__name__)

PRACTICE_URL = "https://api-fxpractice.oanda.com"
LIVE_URL = "https://api-fxtrade.oanda.com"

MAJOR_PAIRS = ("EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "AUD_USD", "USD_CAD", "NZD_USD")


@dataclass(frozen=True)
class Quote:
    pair: str
    bid: Decimal
    ask: Decimal
    time: datetime

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid


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
    """Shared, cached forex rates. All methods are class methods; don't instantiate it.

    Rates for every pair requested so far are fetched together in one request.
    Safe to call from several threads.
    """

    REFRESH_SECONDS = 60 * 60  # refetch rates once they're an hour old
    RETRY_SECONDS = 60  # if a refresh fails, keep the old rates and retry after this

    _client: OandaClient = None
    _quotes: dict[str, Quote] = {}
    _pairs: set[str] = set(MAJOR_PAIRS)
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
            new_pairs = set(wanted) - cls._pairs
            if new_pairs or cls._clock() >= cls._next_refresh:
                cls._refresh(new_pairs)
            missing = set(wanted) - cls._quotes.keys()
            if missing:
                raise KeyError(f"No rate available for {', '.join(sorted(missing))}")
            return {pair: cls._quotes[pair] for pair in wanted}

    @classmethod
    def refresh(cls) -> None:
        """Fetch fresh rates now, without waiting for the hour to pass."""
        with cls._lock:
            cls._refresh(set())

    @classmethod
    def configure(cls, client: OandaClient) -> None:
        """Use a specific client (e.g. in tests) and clear the cache."""
        with cls._lock:
            cls._client = client
            cls._clear()

    @classmethod
    def reset(cls) -> None:
        """Clear the cache and client; the next call fetches from OANDA again."""
        with cls._lock:
            if cls._client:
                cls._client.close()
            cls._client = None
            cls._clear()

    @classmethod
    def _clear(cls) -> None:
        cls._quotes = {}
        cls._pairs = set(MAJOR_PAIRS)
        cls._next_refresh = 0.0

    @classmethod
    def _refresh(cls, new_pairs: set[str]) -> None:
        """Fetch all tracked pairs. Caller must hold the lock."""
        if cls._client is None:
            cls._client = OandaClient.from_env()
        pairs = cls._pairs | new_pairs
        try:
            quotes = cls._client.get_quotes(sorted(pairs))
        except Exception:
            if not cls._quotes:
                raise  # nothing cached to fall back on
            log.warning("Rate refresh failed; using cached rates", exc_info=True)
            cls._next_refresh = cls._clock() + cls.RETRY_SECONDS
            return
        cls._pairs = pairs  # only track new pairs once OANDA has accepted them
        cls._quotes.update({q.pair: q for q in quotes})
        cls._next_refresh = cls._clock() + cls.REFRESH_SECONDS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pairs", nargs="*", default=MAJOR_PAIRS, help="Pairs like EUR_USD (default: majors)")
    args = parser.parse_args()

    for q in RateService.get_quotes(args.pairs).values():
        print(f"{q.pair:<8} bid {q.bid:<10} ask {q.ask:<10} {q.time:%Y-%m-%d %H:%M:%S.%f} UTC")


if __name__ == "__main__":
    main()
