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
  /** A freely licensed photo of the resort (Wikimedia Commons), credited as its licence asks. */
  photo?: { src: string; page: string; author: string; license: string };
}

export const SKI_RESORTS: SkiResort[] = [
  { slug: 'big-sky', name: 'Big Sky', map: true, heat: true, lifts: true, bearing: -100,
    photo: { src: 'https://thumb.wikimedia.org/wikipedia/commons/thumb/b/b8/Lone_Peak%2C_Big_Sky%2C_Montana.jpg/250px-Lone_Peak%2C_Big_Sky%2C_Montana.jpg', page: 'https://commons.wikimedia.org/wiki/File:Lone_Peak,_Big_Sky,_Montana.jpg', author: 'Goldman60', license: 'CC BY 3.0' } },
  { slug: 'bridger-bowl', name: 'Bridger Bowl', map: true, heat: true, lifts: true, bearing: -100,
    photo: { src: 'https://thumb.wikimedia.org/wikipedia/commons/thumb/d/d2/Bridger_Bowl.jpg/250px-Bridger_Bowl.jpg', page: 'https://commons.wikimedia.org/wiki/File:Bridger_Bowl.jpg', author: 'Richard Barley', license: 'CC BY 2.0' } },
  { slug: 'jackson-hole', name: 'Jackson Hole', map: true, heat: true, lifts: true, bearing: -100,
    photo: { src: 'https://thumb.wikimedia.org/wikipedia/commons/thumb/d/de/Jackson_hole_new_tram.jpg/250px-Jackson_hole_new_tram.jpg', page: 'https://commons.wikimedia.org/wiki/File:Jackson_hole_new_tram.jpg', author: 'Enricokamasa', license: 'Public domain' } },
  { slug: 'grand-targhee', name: 'Grand Targhee', map: true, heat: true, lifts: true, bearing: 80,
    photo: { src: 'https://thumb.wikimedia.org/wikipedia/commons/thumb/b/b9/Grand_Targhee_Ski_Resort%2C_February_2021.jpg/250px-Grand_Targhee_Ski_Resort%2C_February_2021.jpg', page: 'https://commons.wikimedia.org/wiki/File:Grand_Targhee_Ski_Resort,_February_2021.jpg', author: 'Richardmouser', license: 'CC BY-SA 4.0' } },
  // The base is on the west side and the lifts climb east, so the camera looks east-northeast.
  { slug: 'revelstoke', name: 'Revelstoke', map: true, heat: true, lifts: true, bearing: 70,
    photo: { src: 'https://thumb.wikimedia.org/wikipedia/commons/thumb/1/16/A_day%27s_skiing_at_spedtacular_Mt_Revelstoke_-_Noth_Americas_biggest_vertical_drop_at_1713m_%285620_ft%29_-_the_mid_mountain_day_lodge_-_%2828620313540%29.jpg/250px-A_day%27s_skiing_at_spedtacular_Mt_Revelstoke_-_Noth_Americas_biggest_vertical_drop_at_1713m_%285620_ft%29_-_the_mid_mountain_day_lodge_-_%2828620313540%29.jpg', page: 'https://commons.wikimedia.org/wiki/File:A_day%27s_skiing_at_spedtacular_Mt_Revelstoke_-_Noth_Americas_biggest_vertical_drop_at_1713m_(5620_ft)_-_the_mid_mountain_day_lodge_-_(28620313540).jpg', author: 'Murray Foubister', license: 'CC BY-SA 2.0' } },
  { slug: 'palisades-tahoe', name: 'Palisades Tahoe', map: false, heat: false, lifts: false, bearing: 0,
    photo: { src: 'https://thumb.wikimedia.org/wikipedia/commons/thumb/3/3c/TheVillageatSquawValley-exterior.jpg/250px-TheVillageatSquawValley-exterior.jpg', page: 'https://commons.wikimedia.org/wiki/File:TheVillageatSquawValley-exterior.jpg', author: 'Jonnyboyca at English Wikipedia', license: 'Public domain' } },
];

export type Feature = 'snow' | 'map' | 'webcams' | 'lifts';

/** The feature tabs a resort has, in order (the first is the default). */
export function featuresFor(resort: SkiResort | null): { key: Feature; label: string }[] {
  if (!resort) {
    return [
      { key: 'snow', label: 'Snow report' },
      { key: 'lifts', label: 'Lifts' },
    ];
  }
  return [
    { key: 'snow', label: 'Snow' },
    ...(resort.map ? [{ key: 'map' as const, label: resort.heat ? 'Heat map' : 'Map' }] : []),
    { key: 'webcams', label: 'Webcams' },
    ...(resort.lifts ? [{ key: 'lifts' as const, label: 'Lifts' }] : []),
  ];
}

/**
 * Everything skiing in one place, by resort, with a tab per feature: today's snow, a 3D map
 * with my powder-day heatmap and the best lift to lap, the webcams, and the lift table.
 * "All resorts" has the snow report and lift table across every ski area. Each tab has its
 * own address (/ski/big-sky/map), and only the open tab loads.
 */
@Component({
  imports: [HeatmapPage, LiftsPage, RouterLink, SiteNav, SnowPage],
  selector: 'app-ski-page',
  styleUrl: './ski-page.css',
  templateUrl: './ski-page.html',
})
export class SkiPage {
  protected readonly resorts = SKI_RESORTS;
  private readonly params = toSignal(inject(ActivatedRoute).paramMap.pipe(map((p) => [p.get('resort'), p.get('feature')])), {
    initialValue: [null, null],
  });
  /** The chosen resort, or null for all of them. */
  protected readonly resort = computed(() => SKI_RESORTS.find((r) => r.slug === this.params()[0]) ?? null);
  protected readonly features = computed(() => featuresFor(this.resort()));
  /** The open tab: the one in the address, if this resort has it, else the first. */
  protected readonly feature = computed<Feature>(() => {
    const asked = this.params()[1];
    return this.features().find((f) => f.key === asked)?.key ?? this.features()[0].key;
  });
  protected readonly slug = computed(() => this.resort()?.slug ?? 'all');
  /** One-item list so each resort gets fresh sections (and a freshly framed map). */
  protected readonly current = computed(() => [this.resort() ?? ALL]);
  protected readonly bestLap = signal<BestLap | null>(null);

  protected setBestLap(lap: BestLap | null): void {
    // The lifts section reports its pick; only keep it while it's for the resort on show.
    this.bestLap.set(lap);
  }
}

const ALL: SkiResort = { slug: 'all', name: 'All resorts', map: false, heat: false, lifts: true, bearing: 0 };
