import { Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { map } from 'rxjs';
import { HeatmapPage } from '../heatmap-page/heatmap-page';
import type { BestLap } from '../heatmap-page/run-heatmap';
import { LiftsPage } from '../lifts-page/lifts-page';
import { SiteNav } from '../site-nav/site-nav';
import { SnowPage } from '../snow-page/snow-page';

/** A ski area on the Skiing page, and what there is for it. */
export interface SkiResort {
  slug: string; // also the snow report's id
  name: string; // as in the lift data and run maps
  map: boolean; // runs and lifts mapped (3D map, best lap)
  heat: boolean; // my powder-day heatmap
  lifts: boolean; // lift vertical data
  bearing: number; // which way the 3D camera looks: up the mountain
}

export const SKI_RESORTS: SkiResort[] = [
  { slug: 'big-sky', name: 'Big Sky', map: true, heat: true, lifts: true, bearing: -100 },
  { slug: 'bridger-bowl', name: 'Bridger Bowl', map: true, heat: true, lifts: true, bearing: -100 },
  { slug: 'jackson-hole', name: 'Jackson Hole', map: true, heat: true, lifts: true, bearing: -100 },
  { slug: 'grand-targhee', name: 'Grand Targhee', map: true, heat: true, lifts: true, bearing: 80 },
  // The base is on the west side and the lifts climb east, so the camera looks east-northeast.
  { slug: 'revelstoke', name: 'Revelstoke', map: true, heat: true, lifts: true, bearing: 70 },
  { slug: 'palisades-tahoe', name: 'Palisades Tahoe', map: false, heat: false, lifts: false, bearing: 0 },
];

/**
 * Everything skiing in one place, by resort: today's snow and webcam, a 3D map with my
 * powder-day heatmap and the best lift to lap, and the lift table. "All resorts" shows the
 * snow report and lift table across every ski area.
 */
@Component({
  imports: [HeatmapPage, LiftsPage, RouterLink, SiteNav, SnowPage],
  selector: 'app-ski-page',
  styleUrl: './ski-page.css',
  templateUrl: './ski-page.html',
})
export class SkiPage {
  protected readonly resorts = SKI_RESORTS;
  private readonly slug = toSignal(inject(ActivatedRoute).paramMap.pipe(map((p) => p.get('resort'))), { initialValue: null });
  /** The chosen resort, or null for all of them. */
  protected readonly resort = computed(() => SKI_RESORTS.find((r) => r.slug === this.slug()) ?? null);
  /** One-item list so each resort gets fresh sections (and a freshly framed map). */
  protected readonly current = computed(() => [this.resort() ?? ALL]);
  protected readonly bestLap = signal<BestLap | null>(null);

  protected setBestLap(lap: BestLap | null): void {
    // The lifts section reports its pick; only keep it while it's for the resort on show.
    this.bestLap.set(lap);
  }
}

const ALL: SkiResort = { slug: 'all', name: 'All resorts', map: false, heat: false, lifts: true, bearing: 0 };
