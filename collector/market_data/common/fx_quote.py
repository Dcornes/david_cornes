from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Quote:
    """An exchange rate, e.g. pair "USD_THB": bid and ask in THB for 1 USD."""

    pair: str
    bid: Decimal
    ask: Decimal
    time: datetime

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid
