import { DatePipe, DecimalPipe } from '@angular/common';
import { AfterViewInit, Component, computed, effect, ElementRef, inject, input, OnDestroy, output, signal, viewChild } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { RouterLink } from '@angular/router';
import type * as MapLibre from 'maplibre-gl';
import { HEAT_STOPS, heatLines, HeatGrid, LatLon } from '../heatmap-page/run-heatmap';
import type { Resort, SnowData, Webcam } from '../snow-page/snow-page';
import type { SkiResort } from './ski-page';

/** What /api/snow/recent returns: new snow on each of the last 7 mornings. */
export interface RecentSnow {
  resort: string;
  source: string | null;
  days: { date: string; inches: number | null }[];
}

/** Marker colour for a day's new snow: grey for none, then deeper blues. */
export function snowColor(inches: number | null): string {
  if (inches === null || inches < 0.5) return '#9aa3ad';
  if (inches < 4) return '#86b6ef';
  if (inches < 8) return '#2a78d6';
  return '#104281';
}

/** The newest morning with a reading (null when none this week). */
export function latestSnow(recent: RecentSnow | undefined): number | null {
  const known = (recent?.days ?? []).filter((d) => d.inches !== null);
  return known.length ? known[known.length - 1].inches : null;
}

const PITCH_3D = 55;

/** Every heat cell's centre as [lat, lon]. */
function cellPoints(grid: HeatGrid): LatLon[] {
  const [lat0, lon0] = grid.center;
  const kx = 111_320 * Math.cos((lat0 * Math.PI) / 180);
  return grid.cells.map(([x, y]) => [lat0 + ((y + 0.5) * grid.cell_m) / 110_540, lon0 + ((x + 0.5) * grid.cell_m) / kx]);
}

/** The point `height × tan(pitch)` metres beyond `center` in the direction the camera faces. */
export function aimPast(center: { lng: number; lat: number }, height: number, pitch: number, bearing: number): [number, number] {
  const distance = height * Math.tan((pitch * Math.PI) / 180);
  const b = (bearing * Math.PI) / 180;
  const north = distance * Math.cos(b);
  const east = distance * Math.sin(b);
  return [center.lng + east / (111_320 * Math.cos((center.lat * Math.PI) / 180)), center.lat + north / 110_540];
}
const MAX_3D_ZOOM = 13.8;
const CAMERA_ICON =
  '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>';
const TERRAIN_TILES = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';
const SATELLITE_TILES = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
// Room for the labels, which sit to the right of each marker.
const OVERVIEW_PADDING = { top: 70, bottom: 80, left: 60, right: 230 };
/** Resorts whose label goes on the left, so close neighbours don't overlap (Targhee by Jackson, Big Sky by Bridger). */
const LABEL_LEFT = new Set(['grand-targhee', 'big-sky']);

/**
 * The Ski Stats map view: every resort on one map, marked with its latest new snow. Selecting
 * one opens a card with today's snow, the last 7 days and its live cam; "Fly in" flies into
 * the mountain in 3D with my powder heat map and the lifts.
 */
@Component({
  imports: [DatePipe, DecimalPipe, RouterLink],
  selector: 'app-resort-map',
  styleUrl: './resort-map.css',
  templateUrl: './resort-map.html',
})
export class ResortMap implements AfterViewInit, OnDestroy {
  readonly resorts = input.required<SkiResort[]>();
  readonly selected = input<string | null>(null); // a resort slug
  readonly choose = output<string | null>(); // the viewer picked a resort (or closed the card)

  private readonly sanitizer = inject(DomSanitizer);
  private readonly element = viewChild.required<ElementRef<HTMLDivElement>>('map');
  protected readonly snow = signal<SnowData | null>(null);
  protected readonly recent = signal<Record<string, RecentSnow>>({});
  protected readonly flown = signal(false); // zoomed into the selected resort in 3D
  protected readonly showHeat = signal(true);
  protected readonly showLifts = signal(true);
  protected readonly showNames = signal(true);
  protected readonly satellite = signal(false);
  protected readonly heatNote = signal<string | null>(null);

  protected readonly resort = computed(() => this.resorts().find((r) => r.slug === this.selected()) ?? null);
  protected readonly report = computed<Resort | null>(
    () => this.snow()?.resorts.find((r) => r.id === this.selected()) ?? null,
  );
  protected readonly week = computed(() => (this.selected() ? this.recent()[this.selected()!] : undefined));
  protected readonly weekMax = computed(() => Math.max(1, ...(this.week()?.days ?? []).map((d) => d.inches ?? 0)));

  private maplibre?: typeof MapLibre;
  private map?: MapLibre.Map;
  private ready = false;
  private markers = new Map<string, { marker: MapLibre.Marker; label: HTMLElement; dot: HTMLElement }>();
  private embeds = new Map<string, SafeResourceUrl>();
  private shownSlug: string | null | undefined = undefined; // the resort the map last moved to

  constructor() {
    effect(() => {
      this.selected();
      this.camIndex.set(null); // a new resort starts at its featured camera
    });
    effect(() => this.paintMarkers(this.recent(), this.snow()));
    // Move the map only when a different resort is picked (not when more data arrives).
    effect(() => {
      const slug = this.selected();
      if (slug !== this.shownSlug) {
        this.shownSlug = slug;
        this.goTo(slug);
      }
    });
    effect(() => {
      this.showNames();
      this.setLayerVisibility(this.showHeat(), this.showLifts(), this.satellite());
    });
  }

  async ngAfterViewInit(): Promise<void> {
    void this.loadSnow();
    for (const r of this.resorts()) {
      void this.loadRecent(r.slug);
    }
    const module = await import('maplibre-gl');
    const ml = ((module as unknown as { default?: typeof MapLibre }).default ?? module) as typeof MapLibre;
    this.maplibre = ml;
    const map = new ml.Map({
      container: this.element().nativeElement,
      center: [-113, 45],
      zoom: 4,
      maxZoom: 16,
      maxPitch: 75,
      scrollZoom: true, // the mouse wheel zooms
      attributionControl: { compact: true },
      style: {
        version: 8,
        // Fonts for the run names, served by the site (public/fonts; Open Sans, Apache 2.0).
        glyphs: `${location.origin}/fonts/{fontstack}/{range}.pbf`,
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
          lifts: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
          runs: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
        },
        layers: [
          { id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-saturation': -0.25, 'raster-opacity': 0.9 } },
          {
            id: 'satellite',
            type: 'raster',
            source: 'satellite',
            layout: { visibility: 'none' },
            paint: { 'raster-saturation': -0.95, 'raster-brightness-min': 0.55, 'raster-contrast': 0.35 },
          },
          { id: 'shade', type: 'hillshade', source: 'shade', paint: { 'hillshade-exaggeration': 0.35 } },
          {
            id: 'run-lines',
            type: 'line',
            source: 'runs',
            filter: ['==', ['get', 'area'], false],
            paint: { 'line-color': '#5f5f5f', 'line-width': 1, 'line-opacity': 0.4 },
          },
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
          // Run names along the runs (named ones only), readable on the map and on satellite.
          {
            id: 'run-names',
            type: 'symbol',
            source: 'runs',
            filter: ['all', ['==', ['get', 'area'], false], ['!=', ['get', 'name'], 'Unnamed run']],
            layout: {
              'symbol-placement': 'line',
              'text-field': ['get', 'name'],
              'text-font': ['Open Sans Semibold'],
              'text-size': ['interpolate', ['linear'], ['zoom'], 11, 10, 15, 13],
              'text-max-angle': 40,
              'symbol-spacing': 300,
            },
            paint: { 'text-color': '#1f2a37', 'text-halo-color': '#ffffff', 'text-halo-width': 1.6 },
          },
        ],
      },
    });
    map.addControl(new ml.NavigationControl({ visualizePitch: true }), 'top-left');
    map.addControl(new ml.FullscreenControl(), 'top-left');
    this.map = map;
    map.on('load', () => {
      this.ready = true;
      this.addMarkers();
      this.paintMarkers(this.recent(), this.snow());
      this.setLayerVisibility(this.showHeat(), this.showLifts(), this.satellite());
      this.shownSlug = this.selected();
      this.goTo(this.selected(), false);
    });
  }

  ngOnDestroy(): void {
    this.map?.remove();
  }

  private async loadSnow(): Promise<void> {
    try {
      const response = await fetch('/api/snow');
      if (response.ok) {
        this.snow.set(await response.json());
      }
    } catch {
      // Markers still show; the card just lacks today's report.
    }
  }

  private async loadRecent(slug: string): Promise<void> {
    try {
      const response = await fetch(`/api/snow/recent?resort=${slug}`);
      if (response.ok) {
        const data: RecentSnow = await response.json();
        this.recent.update((all) => ({ ...all, [slug]: data }));
      }
    } catch {
      // That resort's marker shows no number.
    }
  }

  private addMarkers(): void {
    const ml = this.maplibre!;
    for (const r of this.resorts()) {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'resort-marker';
      el.setAttribute('aria-label', r.name);
      const dot = document.createElement('span');
      dot.className = 'dot';
      // The pill: name, a coloured chip with the new snow, and a camera icon when there are webcams.
      const label = document.createElement('span');
      label.className = 'label';
      const name = document.createElement('span');
      name.className = 'name';
      name.textContent = r.name;
      const chip = document.createElement('span');
      chip.className = 'chip';
      const cam = document.createElement('span');
      cam.className = 'cam-icon';
      cam.innerHTML = CAMERA_ICON; // a fixed SVG, no outside text
      label.append(name, chip, cam);
      el.append(dot, label);
      el.addEventListener('click', (event) => {
        event.stopPropagation();
        this.choose.emit(r.slug);
      });
      const left = LABEL_LEFT.has(r.slug);
      if (left) {
        el.classList.add('label-left');
        el.append(dot); // the dot goes last, on the right
      }
      const marker = new ml.Marker({ element: el, anchor: left ? 'right' : 'left', offset: [left ? 9 : -9, 0] })
        .setLngLat([r.center[1], r.center[0]])
        .addTo(this.map!);
      this.markers.set(r.slug, { marker, label, dot });
    }
  }

  /** Each marker: the resort, its latest new snow (coloured) and a camera if it has webcams. */
  private paintMarkers(recent: Record<string, RecentSnow>, snow: SnowData | null): void {
    const selected = this.selected();
    for (const r of this.resorts()) {
      const m = this.markers.get(r.slug);
      if (!m) {
        continue;
      }
      const inches = latestSnow(recent[r.slug]);
      const cams = snow?.resorts.find((s) => s.id === r.slug)?.webcams.length ?? 0;
      m.dot.style.background = snowColor(inches);
      const chip = m.label.querySelector<HTMLElement>('.chip')!;
      chip.textContent = inches === null ? '' : `${inches}″`;
      chip.style.display = inches === null ? 'none' : '';
      chip.style.background = snowColor(inches);
      chip.classList.toggle('light', inches === null || inches < 4); // dark text on the light colours
      m.label.querySelector<HTMLElement>('.cam-icon')!.style.display = cams ? '' : 'none';
      m.label.title = `${r.name}: ${inches === null ? 'no reading' : `${inches}″ new snow`}${cams ? `, ${cams} webcams` : ''}`;
      m.marker.getElement().classList.toggle('active', selected === r.slug);
    }
  }

  /** Frame every resort, or the chosen one from a little way out. */
  private goTo(slug: string | null, animate = true): void {
    const map = this.map;
    const ml = this.maplibre;
    if (!map || !ml || !this.ready) {
      return;
    }
    this.flown.set(false);
    this.markers.forEach((m) => (m.marker.getElement().style.display = ''));
    this.clearResortLayers();
    map.setTerrain(null);
    const resort = this.resorts().find((r) => r.slug === slug);
    if (resort) {
      map.flyTo({ center: [resort.center[1], resort.center[0]], zoom: 10, pitch: 0, bearing: 0, animate, duration: 1500, padding: this.cardPadding() });
    } else {
      const bounds = new ml.LngLatBounds();
      this.resorts().forEach((r) => bounds.extend([r.center[1], r.center[0]]));
      map.setPadding({ top: 0, bottom: 0, left: 0, right: 0 });
      map.fitBounds(bounds, { padding: OVERVIEW_PADDING, pitch: 0, bearing: 0, animate, duration: 1500 });
    }
  }

  /** Into the mountain in 3D, with my powder heat map (8″+ days, or all days if none) and the lifts. */
  protected async flyIn(): Promise<void> {
    const resort = this.resort();
    const map = this.map;
    if (!resort || !map) {
      return;
    }
    this.flown.set(true);
    this.markers.forEach((m) => (m.marker.getElement().style.display = 'none')); // out of the way in 3D
    map.setTerrain({ source: 'terrain', exaggeration: 1.3 });
    this.heatNote.set(null);
    const name = encodeURIComponent(resort.name);
    const flip = (points: LatLon[]) => points.map(([lat, lon]) => [lon, lat]);
    let liftPoints: LatLon[] = [];
    let heatPoints: LatLon[] = [];
    try {
      const runs = await fetch(`/api/lifts/heatmap/runs?resort=${name}`);
      if (runs.ok && this.flown()) {
        const data: { lifts: { name: string; line: LatLon[] }[]; runs: { name: string; area: boolean; points: LatLon[] }[] } =
          await runs.json();
        (map.getSource('lifts') as MapLibre.GeoJSONSource).setData({
          type: 'FeatureCollection',
          features: data.lifts.map((l) => ({
            type: 'Feature',
            properties: { name: l.name },
            geometry: { type: 'LineString', coordinates: flip(l.line) },
          })),
        });
        (map.getSource('runs') as MapLibre.GeoJSONSource).setData({
          type: 'FeatureCollection',
          features: data.runs.map((r) => ({
            type: 'Feature',
            properties: { name: r.name, area: r.area },
            geometry: r.area ? { type: 'Polygon', coordinates: [flip(r.points)] } : { type: 'LineString', coordinates: flip(r.points) },
          })),
        });
        liftPoints = data.lifts.flatMap((l) => l.line);
      }
      if (resort.heat) {
        let heat = await (await fetch(`/api/lifts/heatmap?resort=${name}&min_snow=8`)).json();
        let note = `Where I've skied on days with 8″+ of new snow (${heat.days} days)`;
        if (!heat.days) {
          heat = await (await fetch(`/api/lifts/heatmap?resort=${name}&min_snow=0`)).json();
          note = `Where I've skied here (all ${heat.days} days; none had 8″+)`;
        }
        if (this.flown()) {
          const grid = heat.heat as HeatGrid;
          (map.getSource('heat') as MapLibre.GeoJSONSource).setData({ type: 'FeatureCollection', features: heatLines(grid) });
          this.heatNote.set(note);
          heatPoints = cellPoints(grid);
        }
      }
    } catch {
      this.heatNote.set("Couldn't load the heat map just now.");
    }
    // Frame where I've skied (the heat), or else the whole ski area, looking up the mountain.
    if (this.flown()) {
      this.frameResort(heatPoints.length ? heatPoints : liftPoints, resort.bearing);
    }
  }

  private frameResort(points: LatLon[], bearing: number): void {
    const map = this.map;
    const ml = this.maplibre;
    if (!map || !ml || !points.length) {
      return;
    }
    const bounds = new ml.LngLatBounds();
    points.forEach(([lat, lon]) => bounds.extend([lon, lat]));
    // The zoom that fits the whole ski area, then pulled back: the mountain stands well above the
    // flat map the fit assumes, so a tilted camera at that zoom would sit in the slope.
    const camera = map.cameraForBounds(bounds, { padding: 40, bearing });
    const zoom = Math.min((camera?.zoom ?? 13) - 0.7, MAX_3D_ZOOM);
    // Centred in the space left of the card.
    const center = bounds.getCenter();
    map.flyTo({ center, zoom, pitch: PITCH_3D, bearing, padding: this.cardPadding(), duration: 2500 });
    // The camera aims at sea level, but the resort stands thousands of feet up, so it shows up
    // nearer than the aim (towards the bottom of the view). Once the terrain has loaded, aim past
    // it by height × tan(tilt), along the view, so the resort itself sits in the middle.
    map.once('idle', () => {
      const height = map.queryTerrainElevation(center);
      if (this.flown() && height) {
        map.easeTo({ center: aimPast(center, height, PITCH_3D, bearing), duration: 900 });
      }
    });
  }

  /** Keep the resort clear of the card, which covers the right side of the map on wide screens. */
  private cardPadding(): { top: number; bottom: number; left: number; right: number } {
    const wide = (this.map?.getContainer().clientWidth ?? 0) > 700;
    return { top: 0, bottom: 0, left: 0, right: wide ? 380 : 0 };
  }

  protected back(): void {
    this.goTo(this.selected());
  }

  private clearResortLayers(): void {
    const map = this.map;
    if (!map || !this.ready) {
      return;
    }
    for (const id of ['heat', 'lifts', 'runs']) {
      (map.getSource(id) as MapLibre.GeoJSONSource).setData({ type: 'FeatureCollection', features: [] });
    }
  }

  private setLayerVisibility(heat: boolean, lifts: boolean, satellite: boolean): void {
    const map = this.map;
    if (!map || !this.ready) {
      return;
    }
    map.setLayoutProperty('heat', 'visibility', heat ? 'visible' : 'none');
    map.setLayoutProperty('lifts', 'visibility', lifts ? 'visible' : 'none');
    map.setLayoutProperty('run-names', 'visibility', this.showNames() ? 'visible' : 'none');
    map.setLayoutProperty('osm', 'visibility', satellite ? 'none' : 'visible');
    map.setLayoutProperty('satellite', 'visibility', satellite ? 'visible' : 'none');
  }

  /** Which webcam the card shows (index into the resort's webcams); starts at the featured one. */
  protected readonly camIndex = signal<number | null>(null);

  protected camera(resort: Resort): Webcam | null {
    const index = this.camIndex() ?? resort.featured_webcam;
    return index === null ? null : (resort.webcams[index] ?? null);
  }

  protected camNumber(resort: Resort): number {
    return (this.camIndex() ?? resort.featured_webcam ?? 0) + 1;
  }

  /** The next or previous webcam, wrapping around. */
  protected stepCam(resort: Resort, step: number): void {
    const count = resort.webcams.length;
    const current = this.camIndex() ?? resort.featured_webcam ?? 0;
    this.camIndex.set((current + step + count) % count);
  }

  protected featured(resort: Resort): Webcam | null {
    return resort.featured_webcam === null ? null : (resort.webcams[resort.featured_webcam] ?? null);
  }

  protected embedUrl(cam: Webcam): SafeResourceUrl {
    let url = this.embeds.get(cam.src);
    if (!url) {
      url = this.sanitizer.bypassSecurityTrustResourceUrl(cam.src);
      this.embeds.set(cam.src, url);
    }
    return url;
  }

  protected imageUrl(cam: Webcam): string {
    return `${cam.src}${cam.src.includes('?') ? '&' : '?'}v=${Math.floor(Date.now() / 300_000)}`;
  }

  protected barHeight(inches: number | null): string {
    return `${((inches ?? 0) / this.weekMax()) * 100}%`;
  }

  protected weekTotal(): number {
    return (this.week()?.days ?? []).reduce((sum, d) => sum + (d.inches ?? 0), 0);
  }
}
