import { AfterViewInit, Component, effect, ElementRef, input, OnDestroy, viewChild } from '@angular/core';
import type * as Leaflet from 'leaflet';

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

/** Cell centers as lat/lon (the server's flat-plane grid around the resort center). */
export function cellLatLons(heat: HeatGrid): { at: LatLon; passes: number }[] {
  const [lat0, lon0] = heat.center;
  const ky = 110_540;
  const kx = 111_320 * Math.cos((lat0 * Math.PI) / 180);
  return heat.cells.map(([x, y, passes]) => ({
    at: [lat0 + ((y + 0.5) * heat.cell_m) / ky, lon0 + ((x + 0.5) * heat.cell_m) / kx] as LatLon,
    passes,
  }));
}

/** 0..1 heat for a cell: log scale, saturating at the busiest few percent of cells. */
export function heatLevel(passes: number, saturate: number): number {
  return Math.min(1, Math.log1p(passes) / Math.log1p(Math.max(saturate, 2)));
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

/**
 * A Strava-style heatmap: where I skied, brighter and redder the more often, drawn on a canvas
 * over the map. Runs (faint grey, with hover names and counts) and lifts (dotted) give context.
 * Leaflet loads only when the map is shown; tiles are OpenStreetMap's.
 */
@Component({
  selector: 'app-run-heatmap',
  styles: `
    :host {
      display: block;
    }

    .map {
      width: 100%;
      height: 38rem;
      border: 1px solid var(--line);
      background: var(--line);
    }

    .map ::ng-deep .leaflet-tile-pane {
      opacity: 0.8;
    }

    .map ::ng-deep .heat-canvas {
      pointer-events: none;
    }

    @media (max-width: 860px) {
      .map {
        height: 26rem;
      }
    }
  `,
  template: `<div #map class="map" role="img" [attr.aria-label]="label()"></div>`,
})
export class RunHeatmap implements AfterViewInit, OnDestroy {
  readonly runs = input.required<RunShape[]>();
  readonly counts = input<Record<string, number>>({});
  readonly heat = input<HeatGrid | null>(null);
  readonly lifts = input<{ name: string; line: LatLon[] }[]>([]);
  readonly label = input('Ski heatmap');

  private readonly element = viewChild.required<ElementRef<HTMLDivElement>>('map');
  private leaflet?: typeof Leaflet;
  private map?: Leaflet.Map;
  private layer?: Leaflet.LayerGroup;
  private canvas?: HTMLCanvasElement;
  private scratch?: HTMLCanvasElement;
  private points: { at: LatLon; passes: number }[] = [];
  private saturate = 2;
  private palette?: Uint8ClampedArray;
  private fitted = false;
  private readonly redraw = () => this.paintHeat();

  constructor() {
    effect(() => this.drawRuns(this.runs(), this.counts(), this.lifts()));
    effect(() => this.setHeat(this.heat()));
  }

  async ngAfterViewInit(): Promise<void> {
    // Leaflet is a CommonJS package: its API arrives as the module's default export.
    const module = await import('leaflet');
    this.leaflet = ((module as unknown as { default?: typeof Leaflet }).default ?? module) as typeof Leaflet;
    const L = this.leaflet;
    // Past zoom 15 the 10 m grid would show as dots, so zoom stops there.
    this.map = L.map(this.element().nativeElement, { scrollWheelZoom: false, maxZoom: 15 });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 15,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(this.map);
    this.layer = L.layerGroup().addTo(this.map);
    // The heat canvas sits over the run lines and is hidden while zooming (redrawn after).
    this.canvas = L.DomUtil.create('canvas', 'heat-canvas leaflet-zoom-hide', this.map.getPanes().overlayPane);
    this.map.on('load moveend zoomend resize viewreset', this.redraw);
    this.drawRuns(this.runs(), this.counts(), this.lifts());
    this.setHeat(this.heat());
  }

  ngOnDestroy(): void {
    this.map?.off('load moveend zoomend resize viewreset', this.redraw);
    this.map?.remove();
  }

  private setHeat(heat: HeatGrid | null): void {
    this.points = heat ? cellLatLons(heat) : [];
    const sorted = this.points.map((p) => p.passes).sort((a, b) => a - b);
    this.saturate = sorted.length ? sorted[Math.floor(sorted.length * 0.97)] : 2;
    if (this.map && this.leaflet && !this.fitted && this.points.length) {
      this.map.fitBounds(this.leaflet.latLngBounds(this.points.map((p) => p.at)), { padding: [30, 30], maxZoom: 14, animate: false });
      this.fitted = true;
    }
    this.paintHeat();
  }

  /** 256 colours along the heat ramp. */
  private heatPalette(): Uint8ClampedArray {
    if (!this.palette) {
      const ramp = document.createElement('canvas');
      ramp.width = 256;
      ramp.height = 1;
      const context = ramp.getContext('2d')!;
      const gradient = context.createLinearGradient(0, 0, 256, 0);
      HEAT_STOPS.forEach(([at, color]) => gradient.addColorStop(at, color));
      context.fillStyle = gradient;
      context.fillRect(0, 0, 256, 1);
      this.palette = context.getImageData(0, 0, 256, 1).data;
    }
    return this.palette;
  }

  /** Draw each cell's heat as a grey level (overlaps keep the hottest), then colour it along the ramp. */
  private paintHeat(): void {
    const [map, canvas, L] = [this.map, this.canvas, this.leaflet];
    if (!map || !canvas || !L) {
      return;
    }
    const size = map.getSize();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = size.x * ratio;
    canvas.height = size.y * ratio;
    canvas.style.width = `${size.x}px`;
    canvas.style.height = `${size.y}px`;
    L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
    const context = canvas.getContext('2d')!;
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (!this.points.length) {
      return;
    }
    // Dots go on a scratch canvas first, then onto this one through a light blur (smooth lines, no pixel steps).
    const scratch = (this.scratch ??= document.createElement('canvas'));
    scratch.width = canvas.width;
    scratch.height = canvas.height;
    const dots = scratch.getContext('2d')!;

    const center = map.getCenter();
    const metersPerPixel = (40_075_016.686 * Math.cos((center.lat * Math.PI) / 180)) / 2 ** (map.getZoom() + 8);
    const cellPx = ((this.heat()?.cell_m ?? 10) / metersPerPixel) * ratio;
    // Round dots a little wider than a cell, so neighbouring cells join into smooth lines.
    const radius = Math.max(1.1 * ratio, cellPx * 0.75);
    const dot = radius * 2;
    dots.globalCompositeOperation = 'lighten';
    for (const { at, passes } of this.points) {
      const p = map.latLngToContainerPoint(at);
      const x = p.x * ratio;
      const y = p.y * ratio;
      if (x < -dot || y < -dot || x > canvas.width + dot || y > canvas.height + dot) {
        continue;
      }
      const level = Math.round(1 + 254 * heatLevel(passes, this.saturate));
      dots.fillStyle = `rgb(${level},${level},${level})`;
      dots.beginPath();
      dots.arc(x, y, radius, 0, Math.PI * 2);
      dots.fill();
    }

    context.filter = `blur(${(0.9 * ratio).toFixed(1)}px)`;
    context.drawImage(scratch, 0, 0);
    context.filter = 'none';
    const image = context.getImageData(0, 0, canvas.width, canvas.height);
    const pixels = image.data;
    const palette = this.heatPalette();
    for (let i = 3; i < pixels.length; i += 4) {
      const alpha = pixels[i];
      if (alpha) {
        const index = pixels[i - 3] * 4; // the grey level picks the colour
        pixels[i - 3] = palette[index];
        pixels[i - 2] = palette[index + 1];
        pixels[i - 1] = palette[index + 2];
        pixels[i] = Math.min(240, alpha * 1.6); // soft edges that fade out
      }
    }
    context.putImageData(image, 0, 0);
  }

  private drawRuns(runs: RunShape[], counts: Record<string, number>, lifts: { name: string; line: LatLon[] }[]): void {
    const L = this.leaflet;
    if (!L || !this.map || !this.layer) {
      return; // not loaded yet; ngAfterViewInit draws once it is
    }
    this.layer.clearLayers();
    for (const run of runs) {
      const count = counts[run.id] ?? 0;
      const level = run.difficulty ? ` · ${DIFFICULTY_LABELS[run.difficulty] ?? run.difficulty}` : '';
      const times = count ? `skied ${count} time${count === 1 ? '' : 's'}` : 'not skied';
      const shape = run.area
        ? L.polygon(run.points, { stroke: false, fillColor: '#7a7a7a', fillOpacity: 0.06 })
        : L.polyline(run.points, { color: '#6f6f6f', weight: 1, opacity: 0.45 });
      shape.bindTooltip(`<strong>${escapeHtml(run.name)}</strong>${escapeHtml(level)}<br>${times}`, { sticky: true }).addTo(this.layer);
    }
    for (const lift of lifts) {
      L.polyline(lift.line, { color: '#3a3a3a', weight: 1.5, opacity: 0.8, dashArray: '2 4' })
        .bindTooltip(escapeHtml(lift.name), { sticky: true })
        .addTo(this.layer);
    }
    if (!this.fitted && runs.length && !this.points.length) {
      this.map.fitBounds(L.latLngBounds(runs.flatMap((r) => r.points)), { padding: [20, 20] });
    }
  }
}
