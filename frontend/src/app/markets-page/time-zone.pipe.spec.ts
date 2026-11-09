import { TimeZonePipe } from './time-zone.pipe';

describe('TimeZonePipe', () => {
  const pipe = new TimeZonePipe();

  it("gives the viewer's zone abbreviation for a timestamp", () => {
    const expected = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' })
      .formatToParts(new Date('2026-11-09T19:00:00Z'))
      .find((p) => p.type === 'timeZoneName')?.value;
    expect(pipe.transform('2026-11-09T19:00:00Z')).toBe(expected);
    expect(pipe.transform('2026-11-09T19:00:00Z')).toMatch(/^([A-Z]{2,5}|GMT|UTC)([+-]\d{1,2}(:\d\d)?)?$/);
  });

  it('accepts epoch milliseconds', () => {
    expect(pipe.transform(Date.parse('2026-11-09T19:00:00Z'))).toBe(pipe.transform('2026-11-09T19:00:00Z'));
  });

  it('is empty for missing or invalid values', () => {
    expect(pipe.transform(null)).toBe('');
    expect(pipe.transform(undefined)).toBe('');
    expect(pipe.transform('not a date')).toBe('');
  });
});
