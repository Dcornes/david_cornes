import { DecimalPipe } from '@angular/common';
import { Component, computed, DestroyRef, effect, inject, input, OnInit, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SiteNav } from '../site-nav/site-nav';
import { LatLon, RouteMap } from './route-map';
import { BestLap, RunHeatmap } from '../heatmap-page/run-heatmap';

export interface Lift {
  resort: string;
  name: string;
  type: string;
  vertical: number; // feet
  ride_time: number; // minutes
  vert_per_minute: number;
}

/** What /api/lifts returns (refreshed daily on the server). */
export interface LiftData {
  fetched_at: string;
  source: string;
  resorts: string[];
  failed: string[];
  lifts: Lift[];
}

/** Your real rides on one lift, from Strava (medians; null where there were no laps). */
export interface MyLift {
  resort: string;
  lift: string; // OpenStreetMap's name
  liftblog_name: string | null; // the same lift in the main table, when matched
  rides: number;
  ride_minutes: number;
  climb_ft: number;
  laps: number;
  ski_minutes: number | null;
  line_minutes: number | null;
  round_trip_minutes: number | null;
  ski_rate_ft_min: number | null;
  lap_vert_per_minute: number | null;
}

/** What /api/lifts/mine returns. */
export interface MyLaps {
  fetched_at: string;
  days: number;
  rides: number;
  laps: number;
  ski_rate_ft_min: number | null;
  lifts: MyLift[];
}

/** A ski route from a lift's top back to its bottom, along mapped runs. */
export interface LiftRoute {
  points: LatLon[];
  length_m: number;
  runs: string[];
  difficulty: string | null; // the hardest run's difficulty, when mapped
}

/** What /api/lifts/routes returns, per resort. */
export interface ResortMap {
  resort: string;
  center: LatLon;
  lifts: { name: string; key: string; line: LatLon[]; route: LiftRoute | null }[];
}

/** 'Shedhorn 4 - High Speed' and 'Shedhorn 4' -> 'shedhorn4' (matches the server's name_key). */
export function liftKey(name: string): string {
  return name
    .toLowerCase()
    .replace(/’/g, "'")
    .replace(/\b(lift|chair|chairlift|express|quad|triple|double|six|high speed|detachable|the)\b/g, ' ')
    .replace(/[^a-z0-9]/g, '');
}

/** A lift with what a day of laps on it looks like, from the page's assumptions. */
export interface LiftDay extends Lift {
  lap_minutes: number; // ride up + ski down + extra
  laps: number; // whole laps that fit in the day
  daily_vertical: number; // feet
  lap_vert_per_minute: number; // vertical over the whole lap: ride up, ski down and extra time
  /** My real laps on this lift (Strava), when there are any; then lap_minutes is my median round trip. */
  my_laps: number | null;
}

export type SortKey =
  | 'vert_per_minute'
  | 'lap_vert_per_minute'
  | 'vertical'
  | 'ride_time'
  | 'name'
  | 'resort'
  | 'lap_minutes'
  | 'laps'
  | 'daily_vertical';

/** Text columns sort A–Z; lap time sorts shortest first; the rest sort highest first. */
const ASCENDING: SortKey[] = ['name', 'resort', 'lap_minutes'];

/** Before the server's first fetch (just after it starts) it answers 503; try again soon. */
const RETRY_MS = 15_000;

/** Defaults for a fast skier on a full day. The viewer can change them on the page. */
export const DEFAULT_DESCENT_RATE = 600; // vertical feet skied per minute
export const DEFAULT_DAY_HOURS = 7; // e.g. 9:00 to 4:00
export const DEFAULT_EXTRA_MINUTES = 1; // loading, unloading, a short line

/**
 * Lap time, laps and vertical for a day of skiing one lift over and over. With my real
 * laps on the lift, the lap is my median round trip (ride, ski down and line); otherwise
 * it's the listed ride time plus the run down at descentRate plus extraMinutes.
 */
export function liftDay(
  lift: Lift,
  descentRate: number,
  dayHours: number,
  extraMinutes: number,
  mine: MyLift | null = null,
): LiftDay {
  const myRoundTrip = mine?.round_trip_minutes ?? null;
  const lapMinutes =
    myRoundTrip ?? lift.ride_time + lift.vertical / Math.max(descentRate, 1) + Math.max(extraMinutes, 0);
  const laps = Math.floor((Math.max(dayHours, 0) * 60) / lapMinutes);
  return {
    ...lift,
    lap_minutes: lapMinutes,
    laps,
    daily_vertical: laps * lift.vertical,
    lap_vert_per_minute: lift.vertical / lapMinutes,
    my_laps: myRoundTrip === null ? null : mine!.laps,
  };
}

@Component({
  imports: [DecimalPipe, RouteMap, RouterLink, RunHeatmap, SiteNav],
  selector: 'app-lifts-page',
  styleUrl: './lifts-page.css',
  templateUrl: './lifts-page.html',
})
export class LiftsPage implements OnInit {
  /** Inside the Skiing page: no header or resort chips; one resort (or all) chosen by the page. */
  readonly embedded = input(false);
  readonly only = input<string | null>(null);
  /** Which way the 3D map looks (the Skiing page passes the resort's). */
  readonly bearing = input(-100);
  /** False when only the best-lap pick is wanted (hidden, behind the heat map tab). */
  readonly showMap = input(true);
  /** The best lap drawn on the map: the lift and its way back down. */
  protected readonly lap = computed<BestLap | null>(() => {
    const best = this.plan()?.best;
    return best ? { lift: best.osm.name, line: best.osm.line, route: best.osm.route!.points } : null;
  });
  /** The best lift to lap at the chosen resort, for the page's map. */
  readonly bestLap = output<BestLap | null>();
  protected readonly data = signal<LiftData | null>(null);
  protected readonly error = signal<string | null>(null);
  /** Your lap times from Strava; null until loaded, or when there are none. */
  protected readonly mine = signal<MyLaps | null>(null);
  /** Lift lines and lap routes per resort, for the route map. */
  protected readonly maps = signal<ResortMap[] | null>(null);
  protected readonly mapResort = signal<string | null>(null);

  /** The best lift to lap at the chosen resort under the current assumptions, with runners-up. */
  protected readonly plan = computed(() => {
    const maps = this.maps();
    const resort = this.mapResort() ?? maps?.[0]?.resort ?? null;
    const map = maps?.find((m) => m.resort === resort);
    if (!map) {
      return null;
    }
    const [rate, hours, extra] = [this.descentRate(), this.dayHours(), this.extraMinutes()];
    const byKey = new Map(map.lifts.filter((l) => l.route).map((l) => [l.key, l]));
    const options = (this.data()?.lifts ?? [])
      .filter((l) => l.resort === resort && byKey.has(liftKey(l.name)))
      .map((l) => {
        const mine = this.myLift(l);
        return { day: liftDay(l, rate, hours, extra, mine), osm: byKey.get(liftKey(l.name))!, mine: mine?.round_trip_minutes ? mine : null };
      })
      .sort((a, b) => b.day.daily_vertical - a.day.daily_vertical);
    return { resort, map, best: options[0] ?? null, others: options.slice(1, 4) };
  });
  /** null = every resort */
  protected readonly resort = signal<string | null>(null);
  protected readonly sort = signal<SortKey>('lap_vert_per_minute');
  protected readonly descentRate = signal(DEFAULT_DESCENT_RATE);
  protected readonly dayHours = signal(DEFAULT_DAY_HOURS);
  protected readonly extraMinutes = signal(DEFAULT_EXTRA_MINUTES);
  private timer?: ReturnType<typeof setTimeout>;
  private mapTimer?: ReturnType<typeof setTimeout>;

  protected readonly lifts = computed(() => {
    const resort = this.resort();
    const key = this.sort();
    const [rate, hours, extra] = [this.descentRate(), this.dayHours(), this.extraMinutes()];
    const lifts = (this.data()?.lifts ?? [])
      .filter((l) => resort === null || l.resort === resort)
      .map((l) => liftDay(l, rate, hours, extra, this.myLift(l)));
    return lifts.sort((a, b) => {
      if (key === 'name' || key === 'resort') {
        return a[key].localeCompare(b[key]);
      }
      return ASCENDING.includes(key) ? a[key] - b[key] : b[key] - a[key];
    });
  });

  /** Bars show vertical per day, scaled to the most shown. */
  protected readonly maxDaily = computed(() => Math.max(1, ...this.lifts().map((l) => l.daily_vertical)));

  /** Set an assumption from a number input, ignoring blanks and nonsense. */
  protected setNumber(target: { set(value: number): void }, raw: string, min: number, max: number): void {
    const value = Number(raw);
    if (raw !== '' && Number.isFinite(value)) {
      target.set(Math.min(Math.max(value, min), max));
    }
  }

  protected resetAssumptions(): void {
    this.descentRate.set(DEFAULT_DESCENT_RATE);
    this.dayHours.set(DEFAULT_DAY_HOURS);
    this.extraMinutes.set(DEFAULT_EXTRA_MINUTES);
  }

  constructor() {
    effect(() => {
      const best = this.plan()?.best;
      this.bestLap.emit(best ? { lift: best.osm.name, line: best.osm.line, route: best.osm.route!.points } : null);
    });
    inject(DestroyRef).onDestroy(() => {
      clearTimeout(this.timer);
      clearTimeout(this.mapTimer);
    });
  }

  ngOnInit(): void {
    if (this.embedded()) {
      this.resort.set(this.only());
      this.mapResort.set(this.only());
    }
    void this.load();
    void this.loadMine();
    void this.loadMaps();
  }

  private async loadMaps(): Promise<void> {
    try {
      const response = await fetch('/api/lifts/routes');
      if (response.ok) {
        this.maps.set((await response.json()).resorts);
        return;
      }
    } catch {
      // Try again below.
    }
    this.mapTimer = setTimeout(() => void this.loadMaps(), 60_000); // maps load a few minutes after the server starts
  }

  /** Your lap times, if the site is connected to Strava (404 otherwise; the section stays hidden). */
  private async loadMine(): Promise<void> {
    try {
      const response = await fetch('/api/lifts/mine');
      if (response.ok) {
        this.mine.set(await response.json());
      }
    } catch {
      // Optional section; nothing to show.
    }
  }

  /** My Strava laps on a lift in the table (matched by resort and Lift Blog name), if any. */
  protected myLift(lift: Lift): MyLift | null {
    return this.mine()?.lifts.find((m) => m.resort === lift.resort && m.liftblog_name === lift.name) ?? null;
  }

  protected useMySpeed(): void {
    const rate = this.mine()?.ski_rate_ft_min;
    if (rate) {
      this.descentRate.set(Math.round(rate / 10) * 10);
    }
  }

  private async load(): Promise<void> {
    try {
      const response = await fetch('/api/lifts');
      if (response.ok) {
        this.data.set(await response.json());
        return;
      }
      this.error.set(response.status === 503 ? 'Fetching lift data…' : "Couldn't load lift data. Trying again shortly.");
    } catch {
      this.error.set("Couldn't load lift data. Trying again shortly.");
    }
    this.timer = setTimeout(() => void this.load(), RETRY_MS);
  }

  protected barWidth(lift: LiftDay): string {
    return `${(lift.daily_vertical / this.maxDaily()) * 100}%`;
  }
}
