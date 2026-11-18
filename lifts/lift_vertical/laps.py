"""Find lift rides in a GPS track and split the day into laps.

A lift ride is a stretch where you stay close to a lift's line and move from near one
end to near the other while climbing. Between one ride and the next:
- line time: the stopped stretch (speed under 1 m/s) just before the next ride;
- ski time: the rest (skiing down, plus any short stops on the way).
A lap is a ride followed by skiing back to the same lift; its round trip runs from the
start of one ride to the start of the next on that lift. Gaps over 45 minutes (lunch,
a break) aren't counted as laps.
"""

import math
import statistics
from dataclasses import dataclass

from .aerialways import Aerialway
from .strava import Track

CORRIDOR_M = 45  # how far from the lift line GPS points can be and still count as riding it
END_FRACTION = 0.15  # "near the bottom/top": within the first/last 15% of the line
ARRIVAL_M = 8  # a ride starts/ends within this distance of the lowest/highest point reached
MIN_CLIMB_M = 40
MIN_RIDE_S, MAX_RIDE_S = 45, 40 * 60
GAP_FILL_S = 30  # brief GPS wobbles off the line during a ride
MAX_LAP_GAP_S = 45 * 60
STOPPED_MPS = 1.0
BOARDING_S = 30  # moving onto the chair just before a ride starts
SHUFFLE_S = 10  # short moves forward while waiting in line
FEET_PER_METER = 3.28084


@dataclass(frozen=True)
class Ride:
    lift: str
    start: float  # seconds into the activity
    end: float
    climb_m: float
    distance_m: float  # mean distance from the lift line (to break ties between parallel lifts)


@dataclass(frozen=True)
class Lap:
    lift: str
    ride_s: float
    ski_s: float
    line_s: float
    descent_m: float
    round_trip_s: float


def smooth(values: list[float], times: list[float], seconds: float = 20) -> list[float]:
    """Running median over about `seconds` (10 s either side by default): steadies GPS
    altitude without shifting it or flattening the top and bottom of a lift ride."""
    result, lo, hi = [], 0, 0
    for i, t in enumerate(times):
        while times[lo] < t - seconds / 2:
            lo += 1
        while hi + 1 < len(times) and times[hi + 1] <= t + seconds / 2:
            hi += 1
        result.append(statistics.median(values[lo: hi + 1]))
    return result


class Projector:
    """Lat/lon to meters on a flat plane around a reference point (fine over a ski area)."""

    def __init__(self, lat0: float, lon0: float) -> None:
        self.lat0, self.lon0 = lat0, lon0
        self.kx = 111_320 * math.cos(math.radians(lat0))
        self.ky = 110_540

    def __call__(self, lat: float, lon: float) -> tuple[float, float]:
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky


def along_line(px: float, py: float, line: list[tuple[float, float]], cumulative: list[float]) -> tuple[float, float]:
    """(distance from the polyline, fraction of the way along it) for a point."""
    best_d, best_s = math.inf, 0.0
    for i in range(len(line) - 1):
        (ax, ay), (bx, by) = line[i], line[i + 1]
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy or 1e-9
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
        d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        if d < best_d:
            best_d, best_s = d, cumulative[i] + t * math.sqrt(length2)
    total = cumulative[-1] or 1e-9
    return best_d, best_s / total


def rides_on(lift: Aerialway, track: Track, xy: list[tuple[float, float]], altitude: list[float], project: Projector) -> list[Ride]:
    line = [project(la, lo) for la, lo in zip(lift.lat, lift.lon)]
    cumulative = [0.0]
    for (ax, ay), (bx, by) in zip(line, line[1:]):
        cumulative.append(cumulative[-1] + math.hypot(bx - ax, by - ay))
    # Only points inside the lift's bounding box (plus the corridor) need the exact check.
    xs, ys = [p[0] for p in line], [p[1] for p in line]
    x0, x1, y0, y1 = min(xs) - CORRIDOR_M, max(xs) + CORRIDOR_M, min(ys) - CORRIDOR_M, max(ys) + CORRIDOR_M
    far = (math.inf, 0.5)
    measures = [along_line(x, y, line, cumulative) if x0 <= x <= x1 and y0 <= y <= y1 else far for x, y in xy]
    if all(m is far for m in measures):
        return []
    near = [d <= CORRIDOR_M for d, _ in measures]

    # Fill short gaps (GPS wobble) between points on the line.
    last_on = None
    for i, on in enumerate(near):
        if on:
            if last_on is not None and i - last_on > 1 and track.time[i] - track.time[last_on] <= GAP_FILL_S:
                for j in range(last_on + 1, i):
                    near[j] = True
            last_on = i

    rides = []
    i, n = 0, len(near)
    while i < n:
        if not near[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and near[j + 1]:
            j += 1
        # Within this stretch on the line, look for end-to-end trips that climb. Progress runs
        # 0 -> 1 along the line, checked both ways since OSM lines aren't always drawn bottom to top.
        tolerance = ARRIVAL_M / (cumulative[-1] or 1e-9)
        for flip in (False, True):
            progress = [1 - f if flip else f for _, f in measures]
            start = None
            for k in range(i, j + 1):
                if progress[k] <= END_FRACTION:
                    start = k
                elif progress[k] >= 1 - END_FRACTION and start is not None:
                    # The zones are generous; trim to when you start moving up the line
                    # (the last point at the bottom) and arrive at the top (the first point there).
                    a = start
                    while a > i and progress[a - 1] <= END_FRACTION:
                        a -= 1
                    lowest = min(progress[a: start + 1])
                    ride_start = max(m for m in range(a, start + 1) if progress[m] <= lowest + tolerance)
                    b = k
                    while b < j and progress[b + 1] >= 1 - END_FRACTION:
                        b += 1
                    highest = max(progress[k: b + 1])
                    ride_end = min(m for m in range(k, b + 1) if progress[m] >= highest - tolerance)

                    duration = track.time[ride_end] - track.time[ride_start]
                    climb = altitude[ride_end] - altitude[ride_start]
                    if climb >= MIN_CLIMB_M and MIN_RIDE_S <= duration <= MAX_RIDE_S:
                        mean_d = statistics.fmean(measures[m][0] for m in range(ride_start, ride_end + 1))
                        rides.append(Ride(lift.name, track.time[ride_start], track.time[ride_end], climb, mean_d))
                    start = None
        i = j + 1
    return rides


def find_rides(track: Track, lifts: list[Aerialway]) -> list[Ride]:
    """Every lift ride in the track, in time order. Where two lifts' rides overlap (lifts
    side by side), the one whose line the track followed more closely wins."""
    if len(track.time) < 2 or not lifts:
        return []
    project = Projector(track.lat[0], track.lon[0])
    xy = [project(la, lo) for la, lo in zip(track.lat, track.lon)]
    altitude = smooth(track.altitude, track.time)
    candidates = sorted((r for lift in lifts for r in rides_on(lift, track, xy, altitude, project)), key=lambda r: r.distance_m)
    chosen: list[Ride] = []
    for ride in candidates:
        if all(ride.end <= c.start or ride.start >= c.end for c in chosen):
            chosen.append(ride)
    return sorted(chosen, key=lambda r: r.start)


def find_laps(track: Track, rides: list[Ride]) -> list[Lap]:
    """A lap for each ride followed by skiing back to the same lift."""
    altitude = smooth(track.altitude, track.time)
    index = {t: i for i, t in enumerate(track.time)}
    laps = []
    for ride, following in zip(rides, rides[1:]):
        gap = following.start - ride.end
        if ride.lift != following.lift or gap <= 0 or gap > MAX_LAP_GAP_S:
            continue
        top, next_start = index[ride.end], index[following.start]
        line_s = 0.0
        if track.speed:
            # Walk back from boarding: skip the first moments of moving onto the chair, then
            # count the stopped stretch, allowing short shuffles forward in the line.
            earliest = None
            k = next_start
            while k > top + 1:
                k -= 1
                if track.speed[k] < STOPPED_MPS:
                    earliest = k
                elif earliest is None:
                    if track.time[next_start] - track.time[k] > BOARDING_S:
                        break
                elif track.time[earliest] - track.time[k] > SHUFFLE_S:
                    break
            if earliest is not None:
                line_s = following.start - track.time[earliest]
        laps.append(Lap(
            lift=ride.lift,
            ride_s=ride.end - ride.start,
            ski_s=gap - line_s,
            line_s=line_s,
            descent_m=max(0.0, altitude[top] - altitude[next_start]),
            round_trip_s=following.start - ride.start,
        ))
    return laps


def summarize_lift(rides: list[Ride], laps: list[Lap]) -> dict:
    """Medians for one lift: ride time over every ride; ski, line and round trip over laps."""
    entry = {
        "rides": len(rides),
        "ride_minutes": statistics.median(r.end - r.start for r in rides) / 60,
        "climb_ft": statistics.median(r.climb_m for r in rides) * FEET_PER_METER,
        "laps": len(laps),
        "ski_minutes": None, "line_minutes": None, "round_trip_minutes": None,
        "ski_rate_ft_min": None, "lap_vert_per_minute": None,
    }
    if laps:
        ski = statistics.median(lap.ski_s for lap in laps) / 60
        descent_ft = statistics.median(lap.descent_m for lap in laps) * FEET_PER_METER
        round_trip = statistics.median(lap.round_trip_s for lap in laps) / 60
        entry.update({
            "ski_minutes": ski,
            "line_minutes": statistics.median(lap.line_s for lap in laps) / 60,
            "round_trip_minutes": round_trip,
            "ski_rate_ft_min": descent_ft / ski if ski > 0 else None,
            "lap_vert_per_minute": descent_ft / round_trip if round_trip > 0 else None,
        })
    return entry
