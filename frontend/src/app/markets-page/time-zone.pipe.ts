import { Pipe, PipeTransform } from '@angular/core';

/** en-US gives names like CST/CDT for U.S. zones, and GMT+1 style offsets elsewhere. */
const ZONE_FORMAT = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' });

/**
 * The viewer's time zone abbreviation at a given moment, e.g. "CST" or "CDT".
 * Worked out per timestamp so it's right on both sides of a daylight saving change.
 */
@Pipe({ name: 'timeZone' })
export class TimeZonePipe implements PipeTransform {
  transform(value: string | number | Date | null | undefined): string {
    if (value === null || value === undefined) {
      return '';
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return '';
    }
    return ZONE_FORMAT.formatToParts(date).find((part) => part.type === 'timeZoneName')?.value ?? '';
  }
}
