import asyncio

from lift_vertical.laps import Ride
from lift_vertical.my_laps import MyLapsCache


class FakeStore:
    """Stands in for PostgresDays."""

    def __init__(self, days=None, fail=False) -> None:
        self.days = dict(days or {})
        self.fail = fail

    def load(self):
        if self.fail:
            raise OSError("no database")
        return dict(self.days)

    def save(self, days) -> None:
        if self.fail:
            raise OSError("no database")
        self.days.update(days)


def day(resort: str):
    return (resort, [Ride("Alpine", 0.0, 500.0, 245.0, 2.0)], [])


def test_sync_merges_both_ways_and_new_days_are_saved(tmp_path) -> None:
    store = FakeStore({1: day("Big Sky")})
    cache = MyLapsCache(tmp_path / "laps.json", store)
    cache.days[2] = day("Bridger Bowl")  # analysed here, not yet in the database

    asyncio.run(cache.sync_store())
    assert set(cache.days) == {1, 2} and set(store.days) == {1, 2}
    assert set(MyLapsCache(tmp_path / "laps.json").days) == {1, 2}  # the file has the database's day too

    cache.days[3] = day("Revelstoke")
    asyncio.run(cache.record(3))
    assert store.days[3] == day("Revelstoke")


def test_an_unreachable_database_doesnt_stop_anything(tmp_path) -> None:
    store = FakeStore(fail=True)
    cache = MyLapsCache(tmp_path / "laps.json", store)
    cache.days[2] = day("Bridger Bowl")
    asyncio.run(cache.sync_store())
    asyncio.run(cache.record(2))
    assert not cache.synced and set(MyLapsCache(tmp_path / "laps.json").days) == {2}

    store.fail = False  # back up: the next check uploads what's missing
    asyncio.run(cache.sync_store())
    assert set(store.days) == {2}
