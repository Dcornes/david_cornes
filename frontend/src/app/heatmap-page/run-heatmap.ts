import { AfterViewInit, Component, effect, ElementRef, input, OnDestroy, signal, viewChild } from '@angular/core';
import type * as MapLibre from 'maplibre-gl';

export type LatLon = [number, number];

/** One run (an OpenStreetMap piste): a line down it, or the outline of a bowl or glade. */
export interface RunShape {
  id: string;
  name: string;
  difficulty: string | null;
  area: boolean;
  points: LatLon[];
}

/** Where I went, summed over the chosen days: [x, y, passes] per cell, x east / y north from center. */
export interface HeatGrid {
  cell_m: number;
  center: LatLon;
  cells: [number, number, number][];
}

/** The heat ramp, yellow-orange to dark red (also used for the table's swatches). */
export const HEAT_STOPS: [number, string][] = [
  [0, '#ffc93c'],
  [0.3, '#ff9a1f'],
  [0.55, '#fc5200'],
  [0.8, '#e0190f'],
  [1, '#8f0000'],
];

export const HEAT_BINS: { min: number; label: string; color: string }[] = [
  { min: 1, label: '1', color: '#ffc93c' },
  { min: 2, label: '2–3', color: '#ff9a1f' },
  { min: 4, label: '4–7', color: '#fc5200' },
  { min: 8, label: '8–15', color: '#e0190f' },
  { min: 16, label: '16+', color: '#8f0000' },
];

export function heatBin(count: number): (typeof HEAT_BINS)[number] | null {
  return count > 0 ? [...HEAT_BINS].reverse().find((b) => count >= b.min)! : null;
}

export const DIFFICULTY_LABELS: Record<string, string> = {
  novice: 'Beginner',
  easy: 'Easy',
  intermediate: 'Intermediate',
  advanced: 'Advanced',
  expert: 'Expert',
  freeride: 'Freeride',
  extreme: 'Extreme',
};

const METERS_PER_DEGREE_LAT = 110_540;
const metersPerDegreeLon = (lat: number) => 111_320 * Math.cos((lat * Math.PI) / 180);

/** 0..1 heat for a cell: log scale, saturating at the busiest few percent of cells. */
export function heatLevel(passes: number, saturate: number): number {
  return Math.min(1, Math.log1p(passes) / Math.log1p(Math.max(saturate, 2)));
}

/** Cell centers as [lon, lat] with their 0..1 heat (log scale, saturating at the busiest few percent). */
export function heatPoints(heat: HeatGrid): { lonLat: [number, number]; level: number }[] {
  if (!heat.cells.length) {
    return [];
  }
  const sorted = heat.cells.map((c) => c[2]).sort((a, b) => a - b);
  const saturate = sorted[Math.floor(sorted.length * 0.97)];
  const [lat0, lon0] = heat.center;
  const kx = metersPerDegreeLon(lat0);
  return heat.cells.map(([x, y, passes]) => ({
    lonLat: [lon0 + ((x + 0.5) * heat.cell_m) / kx, lat0 + ((y + 0.5) * heat.cell_m) / METERS_PER_DEGREE_LAT],
    level: heatLevel(passes, saturate),
  }));
}

const HEAT_BUCKETS = 24;

/**
 * The heat as lines: each cell joined to its occupied neighbours (east, north and both
 * diagonals, so each pair once) by a short segment coloured by the hotter of the two. Segments
 * are grouped into HEAT_BUCKETS heat levels, one feature each, coolest first.
 */
export function heatLines(heat: HeatGrid): GeoJSON.Feature<GeoJSON.MultiLineString, { level: number }>[] {
  const points = heatPoints(heat);
  const index = new Map<string, number>();
  heat.cells.forEach(([x, y], i) => index.set(`${x},${y}`, i));
  const buckets: [number, number][][][] = Array.from({ length: HEAT_BUCKETS }, () => []);
  const bucketOf = (level: number) => Math.min(HEAT_BUCKETS - 1, Math.floor(level * HEAT_BUCKETS));
  const joined = new Set<number>();
  heat.cells.forEach(([x, y], i) => {
    for (const [dx, dy] of [[1, 0], [0, 1], [1, 1], [-1, 1]]) {
      const j = index.get(`${x + dx},${y + dy}`);
      if (j !== undefined) {
        const level = Math.max(points[i].level, points[j].level);
        buckets[bucketOf(level)].push([points[i].lonLat, points[j].lonLat]);
        joined.add(i).add(j);
      }
    }
  });
  points.forEach((p, i) => {
    if (!joined.has(i)) {
      buckets[bucketOf(p.level)].push([p.lonLat, p.lonLat]); // a lone cell: a dot
    }
  });
  return buckets
    .map((segments, b) => ({
      type: 'Feature' as const,
      properties: { level: (b + 0.5) / HEAT_BUCKETS },
      geometry: { type: 'MultiLineString' as const, coordinates: segments },
    }))
    .filter((f) => f.geometry.coordinates.length);
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

export type Basemap = 'map' | 'satellite';

/** The best lift to lap and its way back down, drawn on the map. */
export interface BestLap {
  lift: string;
  line: LatLon[];
  route: LatLon[];
}
export type View = '3d' | '2d';
const PITCH_3D = 60;
const EXAGGERATION = 1.3;

const TERRAIN_TILES = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';
const SATELLITE_TILES = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';

/**
 * A 3D Strava-style heatmap: a map or satellite imagery on terrain (tilted, looking up the
 * mountain), with where I skied laid over it, redder the more often. Runs (faint, hover for name
 * and count) and lifts (dotted) give context. Drag to pan; right-drag or Ctrl-drag to tilt and turn.
 * MapLibre loads only when the map is shown.
 */
@Component({
  selector: 'app-run-heatmap',
  styles: `
    :host {
      display: block;
      position: relative;
    }

    .map {
      width: 100%;
      height: 40rem;
      border: 1px solid var(--line);
      background: #dfe3df;
    }

    .switches {
      position: absolute;
      top: 0.6rem;
      right: 0.6rem;
      z-index: 2;
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 0.4rem;
    }

    .segmented {
      display: inline-flex;
      border-radius: 4px;
      overflow: hidden;
      box-shadow: 0 0 0 2px rgb(0 0 0 / 0.1);
    }

    .segmented button {
      padding: 0.35rem 0.75rem;
      border: 0;
      background: #fff;
      color: #333;
      font: inherit;
      font-size: 0.8125rem;
      font-weight: 600;
      cursor: pointer;
    }

    .segmented button + button {
      border-left: 1px solid #ddd;
    }

    .segmented button.active {
      background: #333;
      color: #fff;
    }

    .hint {
      position: absolute;
      left: 0.6rem;
      bottom: 0.6rem;
      padding: 0.2rem 0.5rem;
      border-radius: 4px;
      background: rgb(255 255 255 / 0.8);
      color: #333;
      font-size: 0.75rem;
      pointer-events: none;
    }

    :host ::ng-deep .maplibregl-popup-content {
      padding: 0.4rem 0.6rem;
      font: inherit;
      font-size: 0.8125rem;
      color: #222;
    }

    @media (max-width: 860px) {
      .map {
        height: 28rem;
      }

      .hint {
        display: none;
      }
    }
  `,
  template: `
    <div #map class="map" role="img" [attr.aria-label]="label()"></div>
    <div class="switches">
      <div class="segmented" role="group" aria-label="2D or 3D">
        <button type="button" [class.active]="view() === '2d'" [attr.aria-pressed]="view() === '2d'" (click)="setView('2d')">
          2D
        </button>
        <button type="button" [class.active]="view() === '3d'" [attr.aria-pressed]="view() === '3d'" (click)="setView('3d')">
          3D
        </button>
      </div>
      <div class="segmented" role="group" aria-label="Base map">
        <button type="button" [class.active]="basemap() === 'map'" [attr.aria-pressed]="basemap() === 'map'" (click)="setBasemap('map')">
          Map
        </button>
        <button
          type="button"
          [class.active]="basemap() === 'satellite'"
          [attr.aria-pressed]="basemap() === 'satellite'"
          (click)="setBasemap('satellite')"
        >
          Satellite
        </button>
      </div>
    </div>
    <span class="hint">{{ view() === '3d' ? 'Scroll to zoom · drag to move · right-drag or Ctrl-drag to tilt and turn' : 'Scroll to zoom · drag to move' }}</span>
  `,
})
export class RunHeatmap implements AfterViewInit, OnDestroy {
  readonly runs = input.required<RunShape[]>();
  readonly counts = input<Record<string, number>>({});
  readonly heat = input<HeatGrid | null>(null);
  readonly lifts = input<{ name: string; line: LatLon[] }[]>([]);
  readonly label = input('Ski heatmap');
  /** Which way the camera looks (degrees from north): west, up the mountain, by default. */
  readonly bearing = input(-100);
  readonly bestLap = input<BestLap | null>(null);
  readonly showHeat = input(true);
  readonly showLap = input(true);
  /** Frame the best lap (with the mountain around it) rather than the runs or heat. */
  readonly frameLap = input(false);
  protected readonly basemap = signal<Basemap>('map');
  protected readonly view = signal<View>('3d');

  private readonly element = viewChild.required<ElementRef<HTMLDivElement>>('map');
  private maplibre?: typeof MapLibre;
  private map?: MapLibre.Map;
  private ready = false;
  private framed = false;

  constructor() {
    effect(() => this.setRuns(this.runs(), this.counts(), this.lifts()));
    effect(() => this.setHeat(this.heat()));
    effect(() => this.setLap(this.bestLap(), this.showLap()));
    effect(() => {
      const visible = this.showHeat();
      if (this.map && this.ready) {
        this.map.setLayoutProperty('heat', 'visibility', visible ? 'visible' : 'none');
      }
    });
  }

  private setLap(lap: BestLap | null, visible: boolean): void {
    const map = this.map;
    if (!map || !this.ready) {
      return;
    }
    const flip = (points: LatLon[]) => points.map(([lat, lon]) => [lon, lat]);
    const features: GeoJSON.Feature[] =
      lap && visible
        ? [
            { type: 'Feature', properties: { kind: 'route' }, geometry: { type: 'LineString', coordinates: flip(lap.route) } },
            { type: 'Feature', properties: { kind: 'lift', name: lap.lift }, geometry: { type: 'LineString', coordinates: flip(lap.line) } },
          ]
        : [];
    (map.getSource('lap') as MapLibre.GeoJSONSource).setData({ type: 'FeatureCollection', features });
    if (lap && this.frameLap()) {
      this.frame([...lap.line, ...lap.route], 1); // a zoom level out, for context
      this.framed = true;
    }
  }

  protected setBasemap(basemap: Basemap): void {
    this.basemap.set(basemap);
    const map = this.map;
    if (map && this.ready) {
      const satellite = basemap === 'satellite';
      map.setLayoutProperty('osm', 'visibility', satellite ? 'none' : 'visible');
      map.setLayoutProperty('satellite', 'visibility', satellite ? 'visible' : 'none');
      map.setLayoutProperty('snow', 'visibility', satellite ? 'visible' : 'none');
      // Snowy relief on the imagery: stronger shading, blue-grey shadows, white highlights.
      map.setPaintProperty('shade', 'hillshade-exaggeration', satellite ? 0.55 : 0.35);
      map.setPaintProperty('shade', 'hillshade-shadow-color', satellite ? '#52607a' : '#3b3b3b');
      map.setPaintProperty('shade', 'hillshade-highlight-color', satellite ? '#ffffff' : '#ffffff');
      map.setPaintProperty('shade', 'hillshade-accent-color', satellite ? '#6b7a94' : '#000000');
      // Runs and lifts in dark slate so they show on the white.
      map.setPaintProperty('run-lines', 'line-color', satellite ? '#3f4d66' : '#5f5f5f');
      map.setPaintProperty('run-lines', 'line-opacity', satellite ? 0.35 : 0.45);
      map.setPaintProperty('lifts', 'line-color', satellite ? '#26303f' : '#2f2f2f');
    }
  }

  /** 3D: terrain, tilted and turned to look up the mountain. 2D: flat, north up, no tilting. */
  protected setView(view: View): void {
    this.view.set(view);
    const map = this.map;
    if (!map || !this.ready) {
      return;
    }
    if (view === '3d') {
      map.setMaxPitch(80);
      map.setTerrain({ source: 'terrain', exaggeration: EXAGGERATION });
      map.dragRotate.enable();
      map.touchZoomRotate.enableRotation();
      map.easeTo({ pitch: PITCH_3D, bearing: this.bearing(), duration: 800 });
    } else {
      map.setTerrain(null);
      map.dragRotate.disable();
      map.touchZoomRotate.disableRotation();
      map.easeTo({ pitch: 0, bearing: 0, duration: 800 });
      map.once('moveend', () => {
        if (this.view() === '2d') {
          map.setMaxPitch(0); // stays flat (unless 3D was picked again meanwhile)
        }
      });
    }
  }

  async ngAfterViewInit(): Promise<void> {
    const module = await import('maplibre-gl');
    const ml = ((module as unknown as { default?: typeof MapLibre }).default ?? module) as typeof MapLibre;
    this.maplibre = ml;
    const [lat, lon] = this.runs()[0]?.points[0] ?? [45.28, -111.4];
    const map = new ml.Map({
      container: this.element().nativeElement,
      center: [lon, lat],
      zoom: 13,
      pitch: PITCH_3D,
      bearing: this.bearing(),
      maxPitch: 80,
      maxZoom: 16, // closer, the 10 m grid would show
      scrollZoom: true, // the mouse wheel zooms
      attributionControl: { compact: true },
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            maxzoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
          },
          satellite: {
            type: 'raster',
            tiles: [SATELLITE_TILES],
            tileSize: 256,
            maxzoom: 18,
            attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community',
          },
          // Separate sources for the terrain and its shading (MapLibre advises not sharing one).
          terrain: {
            type: 'raster-dem',
            tiles: [TERRAIN_TILES],
            encoding: 'terrarium',
            tileSize: 256,
            maxzoom: 15,
            attribution: 'Terrain: Mapzen, AWS Open Data',
          },
          shade: { type: 'raster-dem', tiles: [TERRAIN_TILES], encoding: 'terrarium', tileSize: 256, maxzoom: 15 },
          heat: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
          runs: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
          snow: {
            type: 'geojson',
            data: {
              type: 'Feature',
              properties: {},
              geometry: { type: 'Polygon', coordinates: [[[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]]] },
            },
          },
          lifts: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
          lap: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
        },
        layers: [
          { id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-saturation': -0.25, 'raster-opacity': 0.9 } },
          // The imagery is mostly summer, so it's made to look snowy: grey and bright, under a thin
          // bluish-white veil, with cool shading. Forest stays darker, like trees in snow.
          {
            id: 'satellite',
            type: 'raster',
            source: 'satellite',
            layout: { visibility: 'none' },
            paint: { 'raster-saturation': -0.95, 'raster-brightness-min': 0.55, 'raster-contrast': 0.35 },
          },
          {
            id: 'snow',
            type: 'fill',
            source: 'snow',
            layout: { visibility: 'none' },
            paint: { 'fill-color': '#eef4fc', 'fill-opacity': 0.3 },
          },
          {
            id: 'shade',
            type: 'hillshade',
            source: 'shade',
            paint: { 'hillshade-exaggeration': 0.35, 'hillshade-shadow-color': '#3b3b3b' },
          },
          {
            id: 'run-areas',
            type: 'fill',
            source: 'runs',
            filter: ['==', ['get', 'area'], true],
            paint: { 'fill-color': '#7a7a7a', 'fill-opacity': 0.06 },
          },
          {
            id: 'run-lines',
            type: 'line',
            source: 'runs',
            filter: ['==', ['get', 'area'], false],
            paint: { 'line-color': '#5f5f5f', 'line-width': 1, 'line-opacity': 0.45 },
          },
          // The heat as short rounded segments between neighbouring cells, coloured by heat with the
          // hottest on top; about a cell wide at every zoom and softened at the edges.
          {
            id: 'heat',
            type: 'line',
            source: 'heat',
            layout: { 'line-cap': 'round', 'line-join': 'round', 'line-sort-key': ['get', 'level'] },
            paint: {
              'line-color': ['interpolate', ['linear'], ['get', 'level'], ...HEAT_STOPS.flat()],
              'line-width': ['interpolate', ['exponential', 2], ['zoom'], 11, 1.5, 13, 2.2, 14, 3, 15, 4.5, 16, 6.5],
              'line-blur': ['interpolate', ['linear'], ['zoom'], 11, 0.5, 15, 1.5, 16, 2.5],
              'line-opacity': 0.9,
            },
          },
          {
            id: 'lifts',
            type: 'line',
            source: 'lifts',
            paint: { 'line-color': '#2f2f2f', 'line-width': 1.5, 'line-dasharray': [1, 2], 'line-opacity': 0.85 },
          },
          // The best lap: its lift solid and the way back down dashed, in blue (apart from the heat).
          {
            id: 'lap-route',
            type: 'line',
            source: 'lap',
            filter: ['==', ['get', 'kind'], 'route'],
            layout: { 'line-cap': 'round', 'line-join': 'round' },
            paint: { 'line-color': '#1f5fa8', 'line-width': 3.5, 'line-dasharray': [2, 1.5], 'line-opacity': 0.95 },
          },
          {
            id: 'lap-lift',
            type: 'line',
            source: 'lap',
            filter: ['==', ['get', 'kind'], 'lift'],
            layout: { 'line-cap': 'round' },
            paint: { 'line-color': '#1f5fa8', 'line-width': 4.5 },
          },
          // Invisible wide lines to make thin runs easy to hover.
          {
            id: 'run-hover',
            type: 'line',
            source: 'runs',
            filter: ['==', ['get', 'area'], false],
            paint: { 'line-color': '#000', 'line-width': 10, 'line-opacity': 0 },
          },
        ],
        terrain: { source: 'terrain', exaggeration: EXAGGERATION },
      },
    });
    map.addControl(new ml.NavigationControl({ visualizePitch: true }), 'top-left');
    map.addControl(new ml.FullscreenControl(), 'top-left');
    this.map = map;

    const popup = new ml.Popup({ closeButton: false, closeOnClick: false, offset: 8 });
    map.on('mousemove', 'run-hover', (event) => {
      const props = event.features?.[0]?.properties as { name: string; difficulty: string | null; count: number } | undefined;
      if (!props) {
        return;
      }
      map.getCanvas().style.cursor = 'pointer';
      const level = props.difficulty ? ` · ${DIFFICULTY_LABELS[props.difficulty] ?? props.difficulty}` : '';
      const times = props.count ? `skied ${props.count} time${props.count === 1 ? '' : 's'}` : 'not skied';
      popup
        .setLngLat(event.lngLat)
        .setHTML(`<strong>${escapeHtml(props.name)}</strong>${escapeHtml(level)}<br>${times}`)
        .addTo(map);
    });
    map.on('mouseleave', 'run-hover', () => {
      map.getCanvas().style.cursor = '';
      popup.remove();
    });

    map.on('load', () => {
      this.ready = true;
      this.setBasemap(this.basemap());
      if (this.view() === '2d') {
        this.setView('2d');
      }
      this.setRuns(this.runs(), this.counts(), this.lifts());
      this.setHeat(this.heat());
      this.setLap(this.bestLap(), this.showLap());
      map.setLayoutProperty('heat', 'visibility', this.showHeat() ? 'visible' : 'none');
    });
  }

  ngOnDestroy(): void {
    this.map?.remove();
  }

  private setRuns(runs: RunShape[], counts: Record<string, number>, lifts: { name: string; line: LatLon[] }[]): void {
    const map = this.map;
    if (!map || !this.ready) {
      return; // not loaded yet; the load handler sets them
    }
    const flip = (points: LatLon[]) => points.map(([lat, lon]) => [lon, lat]);
    (map.getSource('runs') as MapLibre.GeoJSONSource).setData({
      type: 'FeatureCollection',
      features: runs.map((run) => ({
        type: 'Feature',
        properties: { name: run.name, difficulty: run.difficulty, area: run.area, count: counts[run.id] ?? 0 },
        geometry: run.area
          ? { type: 'Polygon', coordinates: [flip(run.points)] }
          : { type: 'LineString', coordinates: flip(run.points) },
      })),
    });
    (map.getSource('lifts') as MapLibre.GeoJSONSource).setData({
      type: 'FeatureCollection',
      features: lifts.map((lift) => ({
        type: 'Feature',
        properties: { name: lift.name },
        geometry: { type: 'LineString', coordinates: flip(lift.line) },
      })),
    });
    if (!this.framed && !this.heat()?.cells.length) {
      const points = runs.length ? runs.flatMap((r) => r.points) : lifts.flatMap((l) => l.line);
      if (points.length) {
        this.frame(points);
        this.framed = !runs.length; // with runs, the heat (when it comes) is framed instead
      }
    }
  }

  private setHeat(heat: HeatGrid | null): void {
    const map = this.map;
    if (!map || !this.ready) {
      return;
    }
    const points = heat ? heatPoints(heat) : [];
    (map.getSource('heat') as MapLibre.GeoJSONSource).setData({ type: 'FeatureCollection', features: heat ? heatLines(heat) : [] });
    if (points.length && !this.framed) {
      this.frame(points.map((p) => [p.lonLat[1], p.lonLat[0]]));
      this.framed = true;
    }
  }

  /** Fit these points in a tilted view looking up the mountain. */
  private frame(points: LatLon[], zoomOut = 0): void {
    const map = this.map;
    const ml = this.maplibre;
    if (!map || !ml || !points.length) {
      return;
    }
    const bounds = new ml.LngLatBounds();
    points.forEach(([lat, lon]) => bounds.extend([lon, lat]));
    const camera = map.cameraForBounds(bounds, { padding: 40, bearing: this.view() === '2d' ? 0 : this.bearing() });
    if (camera) {
      const flat = this.view() === '2d';
      map.jumpTo({ ...camera, zoom: (camera.zoom ?? 13) + (flat ? 0.1 : -0.25) - zoomOut, pitch: flat ? 0 : PITCH_3D, bearing: flat ? 0 : this.bearing() });
    }
  }
}
