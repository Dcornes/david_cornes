"""Convert prices from an exchange's local currency (THB, KRW, ...) to the quote you ask for.

Conversions go through USDT with RateService's USDT rates (e.g. USDT_THB, USDT_KRW),
which refresh hourly. Used by exchanges whose markets are priced in a local currency.
"""

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal

from ..oanda.rate_service import RateService
from .fx_quote import Quote
from .top_of_book import split_symbol

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

    @property
    def rate_pairs(self) -> list[str]:
        """Rates needed to turn local-currency prices into this quote."""
        if self.quote == self.local:
            return []
        if self.quote == "USDT":
            return [f"USDT_{self.local}"]
        return [f"USDT_{self.local}", f"USDT_{self.quote}"]


def convert(bid: Decimal, ask: Decimal, target: Target, rates: dict[str, Quote]) -> tuple[Decimal, Decimal]:
    """Local-currency bid/ask -> bid/ask in the target quote, going through USDT.

    Conservative: the bid uses the side of each rate that gives less, the ask the side
    that costs more, so a converted price is never better than what you could get.
    """
    if target.quote == target.local:
        return bid, ask
    usdt_local = rates[f"USDT_{target.local}"]
    bid, ask = bid / usdt_local.ask, ask / usdt_local.bid  # in USDT
    if target.quote != "USDT":
        usdt_quote = rates[f"USDT_{target.quote}"]
        bid, ask = bid * usdt_quote.bid, ask * usdt_quote.ask
    return round_price(bid), round_price(ask)


def round_price(value: Decimal) -> Decimal:
    """Converted prices have long decimals; keep a sensible number of them."""
    step = Decimal("0.01") if value >= 100 else Decimal("0.0001") if value >= 1 else Decimal("0.00000001")
    return value.quantize(step).normalize()


async def targets_for(exchange: str, local: str, symbols: tuple[str, ...], listed_bases: set[str]) -> list[Target]:
    """Pairs an exchange can produce: the base must trade against `local`, and the quote must be convertible.

    Others are skipped with a warning; it's an error only if none are left.
    """
    targets, skipped = [], []
    for symbol in symbols:
        target = Target(*split_symbol(symbol), local)
        if target.base not in listed_bases:
            skipped.append(f"{symbol} (no {target.base}_{local} market)")
            continue
        try:
            await asyncio.to_thread(RateService.get_quotes, target.rate_pairs)
        except KeyError:
            skipped.append(f"{symbol} (no {target.quote} rate)")
            continue
        targets.append(target)
    if not targets:
        raise RuntimeError(f"{exchange} can't provide {', '.join(skipped)}")
    if skipped:
        log.warning("%s skipping %s", exchange, ", ".join(skipped))
    return targets


async def current_rates(targets: list[Target]) -> dict[str, Quote]:
    """Cached rates for these targets (fetched in a thread, since a refresh makes HTTP requests)."""
    pairs = sorted({p for t in targets for p in t.rate_pairs})
    return await asyncio.to_thread(RateService.get_quotes, pairs)
