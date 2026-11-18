"""The shape every resort's snow report takes, plus formatting helpers.

Each resort module turns its own source into a Resort: a few summary stats, webcams
(one featured), and tables of more detail. Values are formatted here, as display
strings, so the website can show any resort the same way.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, tzinfo

DASH = "–"


@dataclass(frozen=True)
class Stat:
    label: str  # e.g. "Last 24 hours"
    value: str  # e.g. '6″'
    detail: str | None = None  # e.g. "Lookout Ridge"


@dataclass(frozen=True)
class Table:
    title: str
    columns: list[str]
    rows: list[list[str]]
    note: str | None = None


@dataclass(frozen=True)
class Webcam:
    name: str
    kind: str  # "image" (a JPEG that's replaced every few minutes) or "embed" (a live player for an iframe)
    src: str  # image URL, or the player's URL
    link: str  # where to see it full size
    updated: str | None = None  # ISO time the image last changed, when known


@dataclass(frozen=True)
class Resort:
    id: str
    name: str
    website: str
    source: str  # who the numbers come from, e.g. "Bridger Bowl"
    source_url: str
    updated: str | None  # ISO time of the newest data, when the source gives one
    summary: list[Stat]
    webcams: list[Webcam] = field(default_factory=list)
    featured_webcam: int | None = None  # index into webcams
    tables: list[Table] = field(default_factory=list)
    message: str | None = None  # e.g. the resort's daily report text
    webcams_page: str | None = None  # the resort's own webcam page, when we can't show its cameras

    def to_dict(self) -> dict:
        return asdict(self)


def number(value) -> float | None:
    """'1,427', 6, '6"' -> a float; '', '--', None -> None."""
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace('"', "").replace("″", "").strip())
    except ValueError:
        return None


def trim(value: float) -> str:
    """11.0 -> '11', 11.75 -> '11.8'."""
    return f"{value:.1f}".rstrip("0").rstrip(".")


def inches(value) -> str:
    n = number(value)
    return DASH if n is None else f"{trim(n)}″"


def inch_range(values) -> str:
    """Several stations' readings as one figure: '6″' or '6–9″'."""
    numbers = sorted(n for n in map(number, values) if n is not None)
    if not numbers:
        return DASH
    low, high = numbers[0], numbers[-1]
    return f"{trim(low)}″" if low == high else f"{trim(low)}–{trim(high)}″"


def degrees_f(value) -> str:
    n = number(value)
    return DASH if n is None else f"{n:.0f}°F"


def mph(value) -> str:
    n = number(value)
    return DASH if n is None else f"{n:.0f} mph"


COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def direction(value) -> str:
    n = number(value)
    return DASH if n is None else f"{COMPASS[round(n % 360 / 45) % 8]} ({n:.0f}°)"


def local_label(time: datetime | None, zone: tzinfo) -> str:
    """'Nov 18, 11:05 AM MST' in the resort's own time zone."""
    if time is None:
        return DASH
    t = time.astimezone(zone)
    hour = t.hour % 12 or 12
    return f"{t:%b} {t.day}, {hour}:{t:%M} {'AM' if t.hour < 12 else 'PM'} {t.tzname()}"
