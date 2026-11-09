"""Pairs an exchange can provide when its markets are priced in a local currency (THB, KRW, ...).

E.g. Bitkub trades BTC against THB; asked for BTC-USDT, it streams BTC_THB and converts
each price with RateService.convert. This module decides which requested pairs are
possible; the conversion itself lives in RateService.
"""

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..common.top_of_book import TopOfBook, clean_decimal, split_symbol
from ..oanda.rate_service import RateService

# (bid, bid size, ask, ask size) in the local currency
Top = tuple[Decimal, Decimal, Decimal, Decimal]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Target:
    """One pair to produce, e.g. BTC priced in USDT, from a market priced in `local` (e.g. KRW)."""

    base: str
    quote: str
    local: str

    @property
    def converted_from(self) -> str | None:
        """The local currency when an exchange rate is needed, for TopOfBook.converted_from."""
        return None if self.quote == self.local else self.local


async def targets_for(exchange: str, local: str, symbols: tuple[str, ...], listed_bases: set[str]) -> list[Target]:
    """Pairs an exchange can produce: the base must trade against `local`, and the quote must be convertible.

    Others are skipped with a warning; it's an error only if none are left.
    """
    targets, skipped = [], []
    for symbol in symbols:
        target = Target(*split_symbol(symbol), local)
        if target.base not in listed_bases:
            skipped.append(f"{symbol} (no {target.base}_{local} market)")
        elif not await asyncio.to_thread(RateService.can_convert, local, target.quote):
            skipped.append(f"{symbol} (no {local} to {target.quote} rate)")
        else:
            targets.append(target)
    if not targets:
        raise RuntimeError(f"{exchange} can't provide {', '.join(skipped)}")
    if skipped:
        log.warning("%s skipping %s", exchange, ", ".join(skipped))
    return targets


def best_levels(bids: Iterable[tuple], asks: Iterable[tuple]) -> Top | None:
    """Highest bid and lowest ask from (price, size) levels, ignoring empty ones; None if a side is empty.

    Doesn't rely on the order levels arrive in (some exchanges list asks highest first).
    """
    bid = max(((clean_decimal(str(p)), clean_decimal(str(q))) for p, q in bids if Decimal(str(q)) > 0), default=None)
    ask = min(((clean_decimal(str(p)), clean_decimal(str(q))) for p, q in asks if Decimal(str(q)) > 0), default=None)
    if bid is None or ask is None:
        return None
    return bid[0], bid[1], ask[0], ask[1]


class ConvertedQuotes:
    """Turns local-currency tops into converted TopOfBook quotes, one per target, skipping unchanged ones."""

    def __init__(self, exchange: str, targets: list[Target], market_id) -> None:
        """market_id(target) -> the exchange's id for that target's local market, e.g. "btc_krw"."""
        self.exchange = exchange
        self.by_market: dict[str, list[Target]] = {}
        for target in targets:
            self.by_market.setdefault(market_id(target), []).append(target)
        self._last: dict[str, Top] = {}

    @property
    def markets(self) -> list[str]:
        return list(self.by_market)

    async def quotes(self, market: str, top: Top | None, timestamp: datetime) -> list[TopOfBook]:
        """Converted quotes for a market's new top of book; empty if it's unknown, empty or unchanged."""
        if market not in self.by_market or top is None or self._last.get(market) == top:
            return []
        self._last[market] = top
        quotes = []
        for target in self.by_market[market]:
            # Cached rates (refreshed hourly); in a thread since a refresh makes HTTP requests.
            bid, ask = await asyncio.to_thread(RateService.convert, top[0], top[2], target.local, target.quote)
            quotes.append(TopOfBook(
                self.exchange, target.base, target.quote, timestamp, bid, top[1], ask, top[3], target.converted_from
            ))
        return quotes
