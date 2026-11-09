"""Combine quotes from every exchange (OKX, Kraken, Binance.US, Crypto.com, Bitstamp, Coinbase, Gemini, Bitfinex, Bitkub, Upbit, Bithumb, KuCoin, Gate, MEXC, Korbit, Coinone, VALR, BTC Markets, Indodax) into one best bid / best ask per pair.

Run from the collector/ directory:

    python -m market_data.market_data_handler BTC-USDT ETH-USDT
    python -m market_data.market_data_handler BTC-USD --exchanges kraken binance
"""

import argparse
import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from .binance.order_book_stream import EXCHANGE as BINANCE
from .binance.order_book_stream import stream_order_books as binance_stream
from .bitfinex.order_book_stream import EXCHANGE as BITFINEX
from .bitfinex.order_book_stream import stream_order_books as bitfinex_stream
from .bitkub.order_book_stream import EXCHANGE as BITKUB
from .bitkub.order_book_stream import stream_order_books as bitkub_stream
from .bithumb.order_book_stream import EXCHANGE as BITHUMB
from .bithumb.order_book_stream import stream_order_books as bithumb_stream
from .bitstamp.order_book_stream import EXCHANGE as BITSTAMP
from .bitstamp.order_book_stream import stream_order_books as bitstamp_stream
from .btcmarkets.order_book_stream import EXCHANGE as BTCMARKETS
from .btcmarkets.order_book_stream import stream_order_books as btcmarkets_stream
from .coinbase.order_book_stream import EXCHANGE as COINBASE
from .coinbase.order_book_stream import stream_order_books as coinbase_stream
from .coinone.order_book_stream import EXCHANGE as COINONE
from .coinone.order_book_stream import stream_order_books as coinone_stream
from .common.top_of_book import TopOfBook, split_symbol
from .cryptocom.order_book_stream import EXCHANGE as CRYPTOCOM
from .cryptocom.order_book_stream import stream_order_books as cryptocom_stream
from .gate.order_book_stream import EXCHANGE as GATE
from .gate.order_book_stream import stream_order_books as gate_stream
from .gemini.order_book_stream import EXCHANGE as GEMINI
from .gemini.order_book_stream import stream_order_books as gemini_stream
from .indodax.order_book_stream import EXCHANGE as INDODAX
from .indodax.order_book_stream import stream_order_books as indodax_stream
from .korbit.order_book_stream import EXCHANGE as KORBIT
from .korbit.order_book_stream import stream_order_books as korbit_stream
from .kraken.order_book_stream import EXCHANGE as KRAKEN
from .kraken.order_book_stream import stream_order_books as kraken_stream
from .kucoin.order_book_stream import EXCHANGE as KUCOIN
from .kucoin.order_book_stream import stream_order_books as kucoin_stream
from .mexc.order_book_stream import EXCHANGE as MEXC
from .mexc.order_book_stream import stream_order_books as mexc_stream
from .okx.order_book_stream import EXCHANGE as OKX
from .okx.order_book_stream import stream_order_books as okx_stream
from .upbit.order_book_stream import EXCHANGE as UPBIT
from .upbit.order_book_stream import stream_order_books as upbit_stream
from .valr.order_book_stream import EXCHANGE as VALR
from .valr.order_book_stream import stream_order_books as valr_stream

log = logging.getLogger(__name__)

# Each stream takes symbols like "BTC-USDT" and yields TopOfBook quotes.
Stream = Callable[..., AsyncIterator[TopOfBook]]

DEFAULT_STREAMS: dict[str, Stream] = {
    "okx": okx_stream,
    "kraken": kraken_stream,
    "binance": binance_stream,
    "cryptocom": cryptocom_stream,
    "bitstamp": bitstamp_stream,
    "coinbase": coinbase_stream,
    "gemini": gemini_stream,
    "bitfinex": bitfinex_stream,
    "bitkub": bitkub_stream,  # THB prices converted to USDT
    "upbit": upbit_stream,  # KRW prices converted to USDT
    "bithumb": bithumb_stream,  # KRW prices converted to USDT
    "kucoin": kucoin_stream,
    "gate": gate_stream,
    "mexc": mexc_stream,
    "korbit": korbit_stream,  # KRW prices converted to USDT
    "coinone": coinone_stream,  # KRW prices converted to USDT
    "valr": valr_stream,  # ZAR prices converted to USDT
    "btcmarkets": btcmarkets_stream,  # AUD prices converted to USDT
    "indodax": indodax_stream,  # IDR prices converted to USDT (polled)
}

# Display name for each stream, shown in feed statuses.
EXCHANGE_NAMES = {
    "okx": OKX,
    "kraken": KRAKEN,
    "binance": BINANCE,
    "cryptocom": CRYPTOCOM,
    "bitstamp": BITSTAMP,
    "coinbase": COINBASE,
    "gemini": GEMINI,
    "bitfinex": BITFINEX,
    "bitkub": BITKUB,
    "upbit": UPBIT,
    "bithumb": BITHUMB,
    "kucoin": KUCOIN,
    "gate": GATE,
    "mexc": MEXC,
    "korbit": KORBIT,
    "coinone": COINONE,
    "valr": VALR,
    "btcmarkets": BTCMARKETS,
    "indodax": INDODAX,
}

# connecting: first connection, no quotes yet; live: quotes arriving;
# reconnecting: the feed stopped and will be retried; stopped: it won't be retried.
FeedState = Literal["connecting", "live", "reconnecting", "stopped"]


@dataclass(frozen=True)
class FeedStatus:
    """Health of one exchange's feed."""

    exchange: str
    state: FeedState
    message: str = ""  # why it stopped, for reconnecting / stopped
    since: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {"exchange": self.exchange, "state": self.state, "message": self.message, "since": self.since.isoformat()}


@dataclass(frozen=True)
class BestBidOffer:
    """Highest bid and lowest ask for one pair across all exchanges."""

    symbol: str
    bid_price: Decimal
    bid_quantity: Decimal
    bid_exchange: str
    ask_price: Decimal
    ask_quantity: Decimal
    ask_exchange: str

    @property
    def spread(self) -> Decimal:
        return self.ask_price - self.bid_price

    @property
    def is_crossed(self) -> bool:
        """True when one exchange's bid is above another's ask (a possible arbitrage)."""
        return self.bid_price > self.ask_price

    def __str__(self) -> str:
        """e.g. 'BTC-USDT  bid 74855.63 x 0.02 (Binance.US)  |  ask 74855.64 x 0.03 (OKX)  spread 0.01'"""
        text = (
            f"{self.symbol:<10} bid {self.bid_price} x {self.bid_quantity} ({self.bid_exchange})  |  "
            f"ask {self.ask_price} x {self.ask_quantity} ({self.ask_exchange})  spread {self.spread}"
        )
        return text + "  CROSSED" if self.is_crossed else text


@dataclass(frozen=True)
class MarketSnapshot:
    """Everything known about one pair: each exchange's latest quote plus the overall best."""

    symbol: str
    quotes: dict[str, TopOfBook]  # exchange -> latest quote
    best: BestBidOffer | None  # None once every exchange's feed has stopped

    def __str__(self) -> str:
        return str(self.best) if self.best else f"{self.symbol:<10} no quotes"

    def to_dict(self) -> dict:
        """JSON-ready form for the website. Numbers are strings so JavaScript doesn't round them."""
        best = self.best
        return {
            "symbol": self.symbol,
            "exchanges": [
                {
                    "exchange": q.exchange,
                    "bid": str(q.bid_price),
                    "bid_quantity": str(q.bid_quantity),
                    "ask": str(q.ask_price),
                    "ask_quantity": str(q.ask_quantity),
                    "timestamp": q.timestamp.isoformat(),
                    "converted_from": q.converted_from,  # e.g. "THB" when an exchange rate was used
                }
                for _, q in sorted(self.quotes.items(), key=lambda item: item[0].lower())  # A-Z, ignoring case
            ],
            "best_bid": best
            and {"price": str(best.bid_price), "quantity": str(best.bid_quantity), "exchange": best.bid_exchange},
            "best_ask": best
            and {"price": str(best.ask_price), "quantity": str(best.ask_quantity), "exchange": best.ask_exchange},
            "spread": best and str(best.spread),
            "crossed": bool(best and best.is_crossed),
        }


class MarketDataHandler:
    """Subscribes to every exchange and tracks the best bid and ask for each pair.

    Keeps the latest TopOfBook from each exchange per pair. Whenever any exchange's
    bid or ask price changes for a pair, it calls on_update with a MarketSnapshot
    (prints by default). Quantity-only changes are skipped; they're too frequent.
    Whenever an exchange's feed changes state (see FeedStatus), it calls on_status
    with every feed's status.
    """

    RECONNECT_MIN_SECONDS = 5
    RECONNECT_MAX_SECONDS = 60

    def __init__(
        self,
        symbols: Iterable[str],
        streams: Mapping[str, Stream] = DEFAULT_STREAMS,
        on_update: Callable[[MarketSnapshot], None] = print,
        *,
        reconnect: bool = False,
        on_status: Callable[[list[FeedStatus]], None] | None = None,
    ) -> None:
        self.symbols = ["-".join(split_symbol(s)) for s in symbols]  # e.g. xbt/usdt -> BTC-USDT
        self.streams = dict(streams)
        self.on_update = on_update
        self.reconnect = reconnect  # restart a feed after it stops (for long-running use)
        self._quotes: dict[str, dict[str, TopOfBook]] = {}  # symbol -> exchange -> latest quote
        self._shown: dict[str, tuple] = {}  # prices last sent per symbol
        self.on_status = on_status
        self._status = {name: FeedStatus(EXCHANGE_NAMES.get(name, name), "connecting") for name in self.streams}

    def statuses(self) -> list[FeedStatus]:
        """Current health of every exchange's feed."""
        return list(self._status.values())

    def _set_status(self, name: str, state: FeedState, message: str = "") -> None:
        current = self._status[name]
        if (current.state, current.message) == (state, message):
            return
        self._status[name] = FeedStatus(current.exchange, state, message)
        log.info("%s feed %s%s", current.exchange, state, f": {message}" if message else "")
        if self.on_status:
            self.on_status(self.statuses())

    def on_quote(self, quote: TopOfBook) -> BestBidOffer | None:
        """Take a quote from any exchange and return the updated best bid/offer for its pair."""
        self._quotes.setdefault(quote.symbol, {})[quote.exchange] = quote
        self._send_if_changed(quote.symbol)
        return self.best(quote.symbol)

    def snapshot(self, symbol: str) -> MarketSnapshot:
        """Each exchange's latest quote for a pair, plus the best bid and ask."""
        symbol = "-".join(split_symbol(symbol))
        return MarketSnapshot(symbol, dict(self._quotes.get(symbol, {})), self.best(symbol))

    def best(self, symbol: str) -> BestBidOffer | None:
        """Highest bid and lowest ask for a pair (any format, e.g. BTC-USDT), or None if no quotes yet."""
        quotes = self._quotes.get("-".join(split_symbol(symbol)))
        if not quotes:
            return None
        # On a price tie, prefer the exchange offering the larger quantity.
        bid = max(quotes.values(), key=lambda q: (q.bid_price, q.bid_quantity))
        ask = min(quotes.values(), key=lambda q: (q.ask_price, -q.ask_quantity))
        return BestBidOffer(
            symbol=bid.symbol,
            bid_price=bid.bid_price,
            bid_quantity=bid.bid_quantity,
            bid_exchange=bid.exchange,
            ask_price=ask.ask_price,
            ask_quantity=ask.ask_quantity,
            ask_exchange=ask.exchange,
        )

    def remove_exchange(self, exchange: str) -> None:
        """Forget an exchange's quotes (e.g. its feed stopped) so stale prices aren't shown."""
        for symbol, quotes in self._quotes.items():
            if quotes.pop(exchange, None):
                self._send_if_changed(symbol)

    async def run(self) -> None:
        """Stream from every exchange at once until cancelled (Ctrl+C)."""
        await asyncio.gather(*(self._consume(name, stream) for name, stream in self.streams.items()))

    async def _consume(self, name: str, stream: Stream) -> None:
        """Feed one exchange's quotes into the handler. A failing exchange doesn't stop the others."""
        delay = self.RECONNECT_MIN_SECONDS
        while True:
            exchanges_seen: set[str] = set()
            try:
                async for quote in stream(*self.symbols):
                    if self._status[name].state != "live":
                        self._set_status(name, "live")
                    exchanges_seen.add(quote.exchange)
                    self.on_quote(quote)
                    delay = self.RECONNECT_MIN_SECONDS  # healthy again
                log.warning("%s feed ended", name)
                reason = "Feed ended"
            except Exception as error:
                log.error("%s feed stopped: %s", name, error)
                reason = str(error) or type(error).__name__
            finally:
                for exchange in exchanges_seen:  # don't show its old prices as current
                    self.remove_exchange(exchange)
            if not self.reconnect:
                self._set_status(name, "stopped", reason)
                return
            self._set_status(name, "reconnecting", reason)  # stays so until quotes arrive again
            log.info("Reconnecting %s in %ss", name, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.RECONNECT_MAX_SECONDS)

    def _send_if_changed(self, symbol: str) -> None:
        quotes = self._quotes.get(symbol, {})
        key = tuple(sorted((exchange, q.bid_price, q.ask_price) for exchange, q in quotes.items()))
        if key != self._shown.get(symbol, ()):
            self._shown[symbol] = key
            self.on_update(self.snapshot(symbol))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+", help="Pairs, e.g. BTC-USDT")
    parser.add_argument("--exchanges", nargs="+", choices=list(DEFAULT_STREAMS), default=list(DEFAULT_STREAMS))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # hide routine request logs
    streams = {name: DEFAULT_STREAMS[name] for name in args.exchanges}
    await MarketDataHandler(args.symbols, streams).run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
