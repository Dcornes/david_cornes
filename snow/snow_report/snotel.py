"""Snow depth from USDA NRCS SNOTEL stations, for resorts whose own snow reports can't be read.

SNOTEL stations report snow depth, snow water equivalent and temperature every hour
through the public AWDB API. A rise in snow depth is a rough guide to new snow, not a
snowfall measurement (snow settles as more falls), so it's labelled "depth change".
"""

from datetime import datetime, timedelta, timezone, tzinfo

import httpx

from .models import DASH, Resort, Stat, Table, Webcam, degrees_f, inches, local_label, trim

API_URL = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data"
STATION_PAGE = "https://wcc.sc.egov.usda.gov/nwcc/site?sitenum={number}"
DATA_ZONE = timezone(timedelta(hours=-8))  # SNOTEL times are Pacific Standard Time all year
ELEMENTS = {"SNWD": "depth", "WTEQ": "swe", "TOBS": "temperature"}
CHANGES = {"Last 24 hours": 24, "Last 48 hours": 48, "Last 7 days": 7 * 24}
DEPTH_NOTE = "A rise in snow depth is a rough guide to new snow; snow settles as more falls."


def series(data: list[dict]) -> dict[str, dict[datetime, float]]:
    """{"depth": {time: inches}, "swe": ..., "temperature": ...} from the AWDB response."""
    result = {name: {} for name in ELEMENTS.values()}
    for station in data:
        for element in station.get("data", []):
            name = ELEMENTS.get(element["stationElement"]["elementCode"])
            if name:
                for point in element.get("values", []):
                    if point.get("value") is not None:
                        time = datetime.fromisoformat(point["date"]).replace(tzinfo=DATA_ZONE)
                        result[name][time] = float(point["value"])
    return result


def change(readings: dict[datetime, float], hours: int) -> float | None:
    """Latest value minus the value `hours` earlier (or the nearest earlier reading)."""
    if not readings:
        return None
    latest = max(readings)
    earlier = [t for t in readings if t <= latest - timedelta(hours=hours)]
    return readings[latest] - readings[max(earlier)] if earlier else None


def signed_inches(value: float | None) -> str:
    if value is None:
        return DASH
    return f"+{trim(value)}″" if value > 0 else f"{trim(value)}″"


def build(
    data: list[dict], *, id: str, name: str, website: str, station_name: str, station_number: int,
    elevation: str, zone: tzinfo, webcams: list[Webcam] | None = None, featured: str | None = None,
    webcams_page: str | None = None,
) -> Resort:
    s = series(data)
    depth, swe, temperature = s["depth"], s["swe"], s["temperature"]
    if not depth:
        raise RuntimeError(f"No snow depth readings from SNOTEL ({station_name})")
    latest = max(depth)

    summary = [
        Stat("Snow depth", inches(depth[latest]), elevation),
        Stat("Depth change", signed_inches(change(depth, 24)), "Last 24 hours"),
    ]
    if swe:
        summary.append(Stat("Water content", inches(swe[max(swe)]), "Snow water equivalent"))

    current = [
        ["Snow depth", inches(depth[latest])],
        ["Snow water equivalent", inches(swe[max(swe)]) if swe else DASH],
        ["Temperature", degrees_f(temperature[max(temperature)]) if temperature else DASH],
        ["Reading", local_label(latest, zone)],
    ]
    rows = [[label, signed_inches(change(depth, hours)), signed_inches(change(swe, hours))] for label, hours in CHANGES.items()]
    webcams = webcams or []
    names = [w.name for w in webcams]
    return Resort(
        id=id, name=name, website=website,
        source=f"USDA NRCS SNOTEL ({station_name} station)", source_url=STATION_PAGE.format(number=station_number),
        updated=latest.isoformat(),
        summary=summary,
        webcams=webcams,
        featured_webcam=names.index(featured) if featured in names else (0 if webcams else None),
        tables=[
            Table(f"SNOTEL station, {elevation}", ["Reading", "Value"], current),
            Table("Changes", ["Period", "Snow depth", "Water equivalent"], rows, DEPTH_NOTE),
        ],
        webcams_page=webcams_page,
    )


async def fetch_data(client: httpx.AsyncClient, triplet: str) -> list[dict]:
    """The last 8 days of hourly readings from one station, e.g. "1082:WY:SNTL"."""
    now = datetime.now(DATA_ZONE)
    params = {
        "stationTriplets": triplet,
        "elements": ",".join(ELEMENTS),
        "duration": "HOURLY",
        "beginDate": f"{(now - timedelta(days=8)):%Y-%m-%d %H:00}",
        "endDate": f"{(now + timedelta(hours=1)):%Y-%m-%d %H:00}",
    }
    response = await client.get(API_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()
