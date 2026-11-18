import asyncio

import httpx
import pytest

from lift_vertical import liftblog
from lift_vertical.liftblog import Lift, fetch_lifts, number, parse_sheet

HEADER = "Status,Lift Name,Type,Manufacturer,Years of Operation,Capacity,Vertical Rise,Length,Horsepower,Line Speed,Chairs,Towers,Drive,Tension,Ride Time,Notes\n"
# Trimmed real rows from Lift Blog's Bridger Bowl sheet.
BRIDGER = HEADER + (
    "Operating,Alpine,Triple,Skytrac,2013-,1800,831,4362,300,550,,17,Bottom,Bottom,7.9,\n"
    "Operating,Bridger,Triple,Doppelmayr CTEC,2010-,1800,\"1,427\",3821,400,500,,20,Bottom,Bottom,7.6,\n"
    "Operating,Secret,Platter,,,,,,,,,,,,,\n"
    "Operating,Schlasman's,Double,,,,,,,,,,,,,\n"
    "Removed,Deer Park,Double,,,,1103,,,,,,,,6.5,\n"
)


def test_number() -> None:
    assert number("1,427") == 1427
    assert number(" 7.9 ") == 7.9
    assert number("") is None


def test_parse_keeps_operating_lifts_with_numbers_and_adds_extras_fastest_first() -> None:
    lifts = parse_sheet("Bridger Bowl", BRIDGER)
    # Secret has no numbers; Deer Park was removed; Schlasman's comes from EXTRA_LIFTS.
    assert [(l.name, l.type) for l in lifts] == [
        ("Schlasman's", "Double"),  # 1427 / 7.067 = 201.9
        ("Bridger", "Triple"),  # 1427 / 7.6 = 187.8
        ("Alpine", "Triple"),  # 831 / 7.9 = 105.2
        ("Ridge Hike", "Hike"),  # 410 / 13 = 31.5
    ]
    assert lifts[1].to_dict() == {
        "resort": "Bridger Bowl", "name": "Bridger", "type": "Triple",
        "vertical": 1427.0, "ride_time": 7.6, "vert_per_minute": 187.8,
    }


def test_fetch_lists_failed_resorts_and_raises_if_all_fail(monkeypatch) -> None:
    monkeypatch.setattr(liftblog, "RESORTS", {"Bridger Bowl": ("good", 1), "Nowhere": ("bad", 2)})

    def handler(request: httpx.Request) -> httpx.Response:
        if "/bad/" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, text=BRIDGER)

    async def fetch(handler) -> dict:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_lifts(client)

    data = asyncio.run(fetch(handler))
    assert (data["resorts"], data["failed"], len(data["lifts"])) == (["Bridger Bowl"], ["Nowhere"], 4)

    with pytest.raises(RuntimeError, match="No lift data"):
        asyncio.run(fetch(lambda request: httpx.Response(500)))


def test_vert_per_minute() -> None:
    assert Lift("X", "Tram", "Tram", 4084, 6.2).vert_per_minute == pytest.approx(658.7, abs=0.1)
