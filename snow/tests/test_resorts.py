import asyncio
from types import SimpleNamespace

from snow_report.models import Resort, Stat, direction, inch_range, inches, local_label
from snow_report.resorts import SnowCache, update


def fake_resort(id: str, value: str):
    async def fetch(client):
        return Resort(id=id, name=id, website="w", source="s", source_url="u", updated=None, summary=[Stat("Snow", value)])

    return SimpleNamespace(ID=id, NAME=id, fetch=fetch)


def failing_resort(id: str):
    async def fetch(client):
        raise RuntimeError("down")

    return SimpleNamespace(ID=id, NAME=id, fetch=fetch)


def test_failed_resort_keeps_its_last_result_marked_stale(monkeypatch) -> None:
    from snow_report import resorts

    monkeypatch.setattr(resorts, "RESORTS", [SimpleNamespace(ID="a"), SimpleNamespace(ID="b")])
    cache = SnowCache()
    assert cache.latest is None

    assert asyncio.run(update(cache, None, [fake_resort("b", "1″"), fake_resort("a", "2″")])) == 2
    assert [r["id"] for r in cache.latest["resorts"]] == ["a", "b"]  # in RESORTS order

    assert asyncio.run(update(cache, None, [fake_resort("a", "3″"), failing_resort("b")])) == 1
    a, b = cache.latest["resorts"]
    assert (a["summary"][0]["value"], a["stale"]) == ("3″", False)
    assert (b["summary"][0]["value"], b["stale"]) == ("1″", True)


def test_formatting() -> None:
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    assert inches("1,427") == "1427″" and inches(11.75) == "11.8″" and inches("--") == "–"
    assert inch_range(['6"', "9", None, "--"]) == "6–9″" and inch_range(["6", "6"]) == "6″"
    assert direction(268) == "W (268°)" and direction(359) == "N (359°)"
    assert local_label(datetime(2026, 7, 7, 21, 55, tzinfo=timezone.utc), ZoneInfo("America/Denver")) == "Jul 7, 3:55 PM MDT"
