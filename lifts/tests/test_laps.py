import pytest

from lift_vertical.aerialways import Aerialway, name_key, parse_overpass, resort_near
from lift_vertical.laps import find_laps, find_rides, summarize_lift
from lift_vertical.strava import Track

# A straight lift running 1 km north from (45.80, -110.90), and a parallel one 500 m east.
NORTH_PER_M = 1 / 110_540
LIFT = Aerialway("Bridger", "chair_lift", (45.80, 45.80 + 1000 * NORTH_PER_M), (-110.90, -110.90))
OTHER = Aerialway("Alpine", "chair_lift", (45.80, 45.80 + 1000 * NORTH_PER_M), (-110.8935, -110.8935))


def day() -> Track:
    """Ride up (6 min, 300 m climb), ski down off to the side (3 min), wait in line (1 min), twice, then ride once more."""
    times, lats, lons, alts, speeds = [], [], [], [], []
    t = 0.0

    def add(n: int, step: float, frac0: float, frac1: float, east_m: float, alt0: float, alt1: float, speed: float):
        nonlocal t
        for i in range(n):
            f = frac0 + (frac1 - frac0) * i / max(n - 1, 1)
            times.append(t)
            lats.append(45.80 + f * 1000 * NORTH_PER_M)
            lons.append(-110.90 + east_m / 77_700)
            alts.append(alt0 + (alt1 - alt0) * i / max(n - 1, 1))
            speeds.append(speed)
            t += step

    for _ in range(3):
        add(73, 5, 0.0, 1.0, 0, 2000, 2300, 3.0)  # ride: 360 s
        add(37, 5, 1.0, 0.0, 150, 2300, 2000, 12.0)  # ski down 150 m east of the lift: 180 s
        add(13, 5, 0.0, 0.0, 0, 2000, 2000, 0.2)  # line at the bottom: 60 s
    return Track(times, lats, lons, alts, speeds)


def test_finds_each_ride_on_the_right_lift() -> None:
    rides = find_rides(day(), [LIFT, OTHER])
    assert [r.lift for r in rides] == ["Bridger"] * 3
    assert all(350 <= r.end - r.start <= 370 for r in rides)
    assert all(r.climb_m == pytest.approx(300, abs=15) for r in rides)


def test_splits_laps_into_ride_ski_and_line_time() -> None:
    track = day()
    laps = find_laps(track, find_rides(track, [LIFT]))
    assert len(laps) == 2  # the last ride has no lap after it
    lap = laps[0]
    assert lap.ride_s == pytest.approx(360, abs=10)
    assert lap.line_s == pytest.approx(60, abs=10)
    assert lap.ski_s == pytest.approx(190, abs=15)  # skiing down (and the step back onto the line)
    assert lap.round_trip_s == pytest.approx(615, abs=10)
    assert lap.descent_m == pytest.approx(300, abs=15)


def test_ignores_skiing_down_under_the_lift() -> None:
    times = [float(i * 5) for i in range(40)]
    track = Track(times, [45.80 + (1 - i / 39) * 1000 * NORTH_PER_M for i in range(40)], [-110.90] * 40,
                  [2300 - 300 * i / 39 for i in range(40)], [12.0] * 40)
    assert find_rides(track, [LIFT]) == []


def test_summary_medians() -> None:
    track = day()
    rides = find_rides(track, [LIFT])
    entry = summarize_lift(rides, find_laps(track, rides))
    assert (entry["rides"], entry["laps"]) == (3, 2)
    assert entry["round_trip_minutes"] == pytest.approx(10.25, abs=0.2)
    assert entry["ski_rate_ft_min"] == pytest.approx(300 * 3.28084 / (190 / 60), rel=0.1)


def test_overpass_parsing_names_and_resorts() -> None:
    body = {"elements": [
        {"type": "way", "tags": {"aerialway": "chair_lift", "name": "Bridger Lift"}, "geometry": [{"lat": 1, "lon": 2}, {"lat": 3, "lon": 4}]},
        {"type": "way", "tags": {"aerialway": "chair_lift"}, "geometry": [{"lat": 1, "lon": 2}]},  # too short
    ]}
    (lift,) = parse_overpass(body)
    assert (lift.name, lift.lat, lift.lon) == ("Bridger Lift", (1, 3), (2, 4))
    assert name_key("Bridger Lift") == name_key("Bridger") == "bridger"
    assert name_key("Pierre's Knob Chair") == name_key("Pierre's Knob")
    assert resort_near(45.82, -110.90) == "Bridger Bowl" and resort_near(40.0, -100.0) is None
