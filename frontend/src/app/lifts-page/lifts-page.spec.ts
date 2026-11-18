import { Lift, liftDay } from './lifts-page';

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
