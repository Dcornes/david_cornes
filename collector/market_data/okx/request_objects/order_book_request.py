import json
from dataclasses import asdict, dataclass, field
from typing import Literal

# OKX order book channels. books / books5 / bbo-tbt are public (no login);
# books-l2-tbt and books50-l2-tbt require a logged-in connection.
OrderBookChannel = Literal["books", "books5", "bbo-tbt", "books-l2-tbt", "books50-l2-tbt"]


@dataclass
class Instrument:
    channel: OrderBookChannel
    instId: str


@dataclass
class OrderBookRequest:
    """An OKX WebSocket (un)subscribe message, e.g.

    {"id": "1", "op": "subscribe", "args": [{"channel": "books5", "instId": "BTC-USDT"}]}
    """

    args: list[Instrument]
    op: Literal["subscribe", "unsubscribe"] = "subscribe"
    # Optional client-chosen id (1-32 alphanumeric chars) that OKX echoes back.
    id: str | None = field(default=None)

    @classmethod
    def subscribe(cls, *inst_ids: str, channel: OrderBookChannel = "books5", id: str | None = None) -> "OrderBookRequest":
        return cls(args=[Instrument(channel, inst_id) for inst_id in inst_ids], id=id)

    @classmethod
    def unsubscribe(cls, *inst_ids: str, channel: OrderBookChannel = "books5", id: str | None = None) -> "OrderBookRequest":
        return cls(args=[Instrument(channel, inst_id) for inst_id in inst_ids], op="unsubscribe", id=id)

    def to_dict(self) -> dict:
        message: dict = {"op": self.op, "args": [asdict(arg) for arg in self.args]}
        if self.id is not None:
            message = {"id": self.id, **message}
        return message

    def to_json(self) -> str:
        return json.dumps(self.to_dict())
