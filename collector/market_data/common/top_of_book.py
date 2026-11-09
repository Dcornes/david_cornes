"""Best bid and ask for one instrument, shared by every exchange feed."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

# Exchange-specific asset codes mapped to the common code. Kraken calls Bitcoin XBT.
ASSET_ALIASES = {"XBT": "BTC"}


def normalize_asset(asset: str) -> str:
    """Upper-case an asset code and map aliases, e.g. "xbt" -> "BTC"."""
    asset = asset.upper()
    return ASSET_ALIASES.get(asset, asset)


def split_symbol(symbol: str) -> tuple[str, str]:
    """Split a pair written as BTC-USD, BTC/USD or BTC_USD into ("BTC", "USD")."""
    parts = re.split(r"[-/_]", symbol)
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Expected a pair like BTC-USD, got {symbol!r}")
    return normalize_asset(parts[0]), normalize_asset(parts[1])


def clean_decimal(value: str) -> Decimal:
    """Parse a padded number ("74818.01000000") without the trailing zeros."""
    number = Decimal(value).normalize()
    return number.quantize(Decimal(1)) if number.as_tuple().exponent > 0 else number


@dataclass(frozen=True)
class TopOfBook:
    exchange: str  # source exchange, e.g. "OKX"
    symbol: str = field(init=False)  # always "<instrument_1>-<instrument_2>", e.g. "BTC-USD"
    instrument_1: str  # base asset: BTC in BTC-USD
    instrument_2: str  # quote asset: USD in BTC-USD
    timestamp: datetime  # exchange time of the update, in UTC
    bid_price: Decimal
    bid_quantity: Decimal
    ask_price: Decimal
    ask_quantity: Decimal
    # Set when prices were converted with an exchange rate, e.g. "THB" for Bitkub's
    # baht prices shown in USDT. None when the exchange quotes the pair directly.
    converted_from: str | None = None

    def __post_init__(self) -> None:
        # Same codes and symbol format whichever exchange the data came from (XBT -> BTC).
        object.__setattr__(self, "instrument_1", normalize_asset(self.instrument_1))
        object.__setattr__(self, "instrument_2", normalize_asset(self.instrument_2))
        object.__setattr__(self, "symbol", f"{self.instrument_1}-{self.instrument_2}")

    def __str__(self) -> str:
        """e.g. '2026-11-09 14:55:18.261 UTC  OKX  BTC-USDT  bid 0.52 @ 81308.7 | ask 81308.8 x 0.82'"""
        time = f"{self.timestamp:%Y-%m-%d %H:%M:%S.%f}"[:-3]  # milliseconds
        text = (
            f"{time} UTC  {self.exchange:<10} {self.symbol:<12} "
            f"bid {self.bid_quantity} @ {self.bid_price}  |  ask {self.ask_price} x {self.ask_quantity}"
        )
        return f"{text}  (converted from {self.converted_from})" if self.converted_from else text
