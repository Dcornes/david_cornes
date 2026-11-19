import { DecimalPipe } from '@angular/common';
import { Component, computed, DestroyRef, inject, input, OnInit, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SiteNav } from '../site-nav/site-nav';
import { BestLap, DIFFICULTY_LABELS, HEAT_STOPS, heatBin, HeatGrid, LatLon, RunHeatmap, RunShape } from './run-heatmap';

/** What /api/lifts/heatmap/runs returns: a resort's runs and lifts (OpenStreetMap). */
export interface ResortRuns {
  resort: string;
  resorts: string[];
  center: LatLon;
  runs: RunShape[];
  lifts: { name: string; line: LatLon[] }[];
}

/** What /api/lifts/heatmap returns: run id -> descents, on days with enough new snow. */
export interface Heatmap {
  resort: string;
  min_snow: number;
  snow_source: { name: string; url: string } | null; // the resort's own snow reports, when used
  station: { name: string; elevation_ft: number | null; url: string } | null;
  backup_stations: { name: string; elevation_ft: number | null; url: string }[];
  days_with_runs: number;
  days_unknown_snow: number;
  days: number;
  snow_amounts: number[]; // new snow on each of my days there, largest first
  descents: number;
  counts: Record<string, number>;
  heat: HeatGrid;
}

export interface RunRow {
  name: string;
  difficulty: string | null;
  times: number;
}

/**
 * Runs by name with how many times each was skied. A named run is often several mapped
 * pieces; one descent can cover more than one, so a run's count is its busiest piece's.
 */
export function runTable(runs: RunShape[], counts: Record<string, number>): RunRow[] {
  const byName = new Map<string, RunRow>();
  for (const run of runs) {
    const times = counts[run.id] ?? 0;
    if (!times || run.name === 'Unnamed run') {
      continue;
    }
    const row = byName.get(run.name);
    if (!row || times > row.times) {
      byName.set(run.name, { name: run.name, difficulty: run.difficulty ?? row?.difficulty ?? null, times });
    }
  }
  return [...byName.values()].sort((a, b) => b.times - a.times || a.name.localeCompare(b.name));
}

export const DEFAULT_MIN_SNOW = 8; // inches
export const HEATMAP_RESORTS = ['Big Sky', 'Bridger Bowl'];

@Component({
  imports: [DecimalPipe, RouterLink, RunHeatmap, SiteNav],
  selector: 'app-heatmap-page',
  styleUrl: './heatmap-page.css',
  templateUrl: './heatmap-page.html',
})
export class HeatmapPage implements OnInit {
  /** Inside the Skiing page: no header or resort toggle; the page picks the resort. */
  readonly embedded = input(false);
  readonly only = input<string | null>(null);
  /** Whether there's powder heat for this resort (otherwise just the map). */
  readonly heatAvailable = input(true);
  readonly bearing = input(-100);
  readonly bestLap = input<BestLap | null>(null);
  protected readonly showHeat = signal(true);
  protected readonly showLap = signal(true);
  protected readonly ramp = `linear-gradient(to right, ${HEAT_STOPS.map(([at, c]) => `${c} ${at * 100}%`).join(', ')})`;
  protected readonly difficulty = DIFFICULTY_LABELS;
  protected readonly defaultSnow = DEFAULT_MIN_SNOW;
  protected readonly resorts = HEATMAP_RESORTS;
  protected readonly resort = signal(HEATMAP_RESORTS[0]);
  protected readonly minSnow = signal(DEFAULT_MIN_SNOW);
  protected readonly shapes = signal<ResortRuns | null>(null);
  protected readonly heat = signal<Heatmap | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly loading = signal(false);
  protected readonly showAll = signal(false);
  /** Set when none of my days here reached the default, so the slider started at 0 instead. */
  protected readonly lowered = signal(false);
  private firstHeat = true;
  private timer?: ReturnType<typeof setTimeout>;
  private request = 0;

  /** Days that clear the threshold right now (from the published amounts, before the counts arrive). */
  protected readonly matchingDays = computed(() => this.heat()?.snow_amounts.filter((a) => a >= this.minSnow()).length ?? 0);
  protected readonly table = computed(() => runTable(this.shapes()?.runs ?? [], this.heat()?.counts ?? {}));
  protected readonly runsSkied = computed(() => this.table().length);
  protected readonly heatBin = heatBin;
  /** The current resort's map as a one-item list, so the map is rebuilt (and re-framed) per resort. */
  protected readonly mapFor = computed(() => {
    const shapes = this.shapes();
    return shapes && shapes.resort === this.resort() ? [shapes] : [];
  });

  constructor() {
    inject(DestroyRef).onDestroy(() => clearTimeout(this.timer));
  }

  ngOnInit(): void {
    if (this.embedded() && this.only()) {
      this.resort.set(this.only()!);
    }
    void this.loadShapes();
    if (this.heatAvailable()) {
      void this.loadHeat();
    }
  }

  protected setResort(resort: string): void {
    if (resort === this.resort()) {
      return;
    }
    this.resort.set(resort);
    this.shapes.set(null);
    this.heat.set(null);
    this.showAll.set(false);
    void this.loadShapes();
    void this.loadHeat();
  }

  protected setMinSnow(raw: string): void {
    const value = Number(raw);
    if (raw === '' || !Number.isFinite(value)) {
      return;
    }
    this.minSnow.set(Math.min(Math.max(Math.round(value), 0), 36));
    clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.loadHeat(), 200);
  }

  private async loadShapes(): Promise<void> {
    try {
      const resort = this.resort();
      const response = await fetch(`/api/lifts/heatmap/runs?resort=${encodeURIComponent(resort)}`);
      if (response.ok) {
        const shapes: ResortRuns = await response.json();
        if (resort === this.resort()) {
          this.shapes.set(shapes);
        }
        return;
      }
      this.error.set("Couldn't load the run map.");
    } catch {
      this.error.set("Couldn't load the run map.");
    }
  }

  private async loadHeat(): Promise<void> {
    const request = ++this.request;
    this.loading.set(true);
    try {
      const params = new URLSearchParams({ resort: this.resort(), min_snow: String(this.minSnow()) });
      const response = await fetch(`/api/lifts/heatmap?${params}`);
      if (request !== this.request) {
        return; // a newer threshold was asked for meanwhile
      }
      if (response.ok) {
        const heat: Heatmap = await response.json();
        if (this.firstHeat && !heat.days && heat.snow_amounts.length && this.minSnow() > 0) {
          // No deep days here: start from all my days instead of an empty map.
          this.firstHeat = false;
          this.lowered.set(true);
          this.minSnow.set(0);
          void this.loadHeat();
          return;
        }
        this.firstHeat = false;
        this.heat.set(heat);
        this.error.set(null);
      } else {
        this.error.set(
          response.status === 404 ? 'No ski days with runs yet.' : "Couldn't load snow history. Try again shortly.",
        );
      }
    } catch {
      this.error.set("Couldn't load the heatmap. Try again shortly.");
    } finally {
      if (request === this.request) {
        this.loading.set(false);
      }
    }
  }
}
