import { myExtraMinutes, Lift, liftDay } from './lifts-page';

const BRIDGER: Lift = { resort: 'Bridger Bowl', name: 'Bridger', type: 'Triple', vertical: 1427, ride_time: 7.6, vert_per_minute: 187.8 };

describe('liftDay', () => {
  it('adds the ride, the run down and extra time into a lap, and counts whole laps in the day', () => {
    // 7.6 min up + 1427 / 600 = 2.38 min down + 1 min extra = 10.98 min a lap; 420 / 10.98 = 38.3 laps.
    const day = liftDay(BRIDGER, 600, 7, 1);
    expect(day.lap_minutes).toBeCloseTo(10.98, 2);
    expect(day.laps).toBe(38);
    expect(day.daily_vertical).toBe(38 * 1427);
    expect(day.lap_vert_per_minute).toBeCloseTo(1427 / 10.98, 0); // about 130 ft a minute, vs 188 counting only the ride
  });

  it('gives more laps to a faster skier and fewer in a shorter day', () => {
    expect(liftDay(BRIDGER, 1200, 7, 1).laps).toBeGreaterThan(38);
    expect(liftDay(BRIDGER, 600, 3.5, 1).laps).toBe(19);
  });

  it('never divides by zero or counts negative laps', () => {
    expect(Number.isFinite(liftDay(BRIDGER, 0, 7, 0).lap_minutes)).toBe(true);
    expect(liftDay(BRIDGER, 600, -1, -5).laps).toBe(0);
  });
});

describe('myExtraMinutes', () => {
  it('is the median of my real laps minus ride and ski-down time, to the half minute', () => {
    const lift = (name: string, vertical: number, ride: number): Lift => ({ resort: 'R', name, type: 'Quad', vertical, ride_time: ride, vert_per_minute: 0 });
    const lifts = [lift('A', 1000, 5), lift('B', 500, 4), lift('C', 2000, 8)];
    const mine = (name: string, trip: number, laps = 10) => ({ resort: 'R', liftblog_name: name, round_trip_minutes: trip, laps });
    // At 250 ft/min the formula gives A 9, B 6, C 16 minutes; real laps are 1, 3 and 0.5 longer.
    const data = { ski_rate_ft_min: 250, lifts: [mine('A', 10), mine('B', 9), mine('C', 16.5), mine('A', 99, 1)] } as never;
    expect(myExtraMinutes(data, lifts)).toBe(1);
    expect(myExtraMinutes(null, lifts)).toBeNull();
  });
});
