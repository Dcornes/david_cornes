import { AfterViewInit, Component, effect, ElementRef, input, OnDestroy, viewChild } from '@angular/core';
import type * as Leaflet from 'leaflet';

export type LatLon = [number, number];

/** One lift on the map: its line, and (for the chosen lift) the route back to its bottom. */
export interface MapLift {
  name: string;
  line: LatLon[];
}

/**
 * A resort map: every lift as a thin line, the chosen lift highlighted, and its lap route.
 * Leaflet is loaded only when the map is first shown. Tiles are OpenStreetMap's.
 */
@Component({
  selector: 'app-route-map',
  styles: `
    :host {
      display: block;
    }

    .map {
      width: 100%;
      height: 28rem;
      border: 1px solid var(--line);
      background: var(--line);
    }

    @media (max-width: 860px) {
      .map {
        height: 20rem;
      }
    }
  `,
  template: `<div #map class="map" role="img" [attr.aria-label]="label()"></div>`,
})
export class RouteMap implements AfterViewInit, OnDestroy {
  readonly lifts = input.required<MapLift[]>();
  readonly chosen = input<string | null>(null); // the chosen lift's name
  readonly route = input<LatLon[] | null>(null);
  readonly label = input('Ski area map');

  private readonly element = viewChild.required<ElementRef<HTMLDivElement>>('map');
  private leaflet?: typeof Leaflet;
  private map?: Leaflet.Map;
  private layer?: Leaflet.LayerGroup;

  constructor() {
    // Redraw when the resort, chosen lift or route changes.
    effect(() => {
      const [lifts, chosen, route] = [this.lifts(), this.chosen(), this.route()];
      this.draw(lifts, chosen, route);
    });
  }

  async ngAfterViewInit(): Promise<void> {
    // Leaflet is a CommonJS package: its API arrives as the module's default export.
    const module = await import('leaflet');
    this.leaflet = ((module as unknown as { default?: typeof Leaflet }).default ?? module) as typeof Leaflet;
    const L = this.leaflet;
    this.map = L.map(this.element().nativeElement, { scrollWheelZoom: true, attributionControl: true });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(this.map);
    this.layer = L.layerGroup().addTo(this.map);
    this.draw(this.lifts(), this.chosen(), this.route());
  }

  ngOnDestroy(): void {
    this.map?.remove();
  }

  private draw(lifts: MapLift[], chosen: string | null, route: LatLon[] | null): void {
    const L = this.leaflet;
    if (!L || !this.map || !this.layer) {
      return; // not loaded yet; ngAfterViewInit draws once it is
    }
    this.layer.clearLayers();
    for (const lift of lifts) {
      if (lift.name !== chosen) {
        L.polyline(lift.line, { color: '#4a4a4a', weight: 2, opacity: 0.55 }).bindTooltip(lift.name).addTo(this.layer);
      }
    }
    const focus: LatLon[] = [];
    if (route?.length) {
      L.polyline(route, { color: '#1f5fa8', weight: 4, opacity: 0.9, dashArray: '8 6' }).bindTooltip('Ski route').addTo(this.layer);
      focus.push(...route);
    }
    const lift = lifts.find((l) => l.name === chosen);
    if (lift) {
      L.polyline(lift.line, { color: '#a3392c', weight: 5 }).bindTooltip(lift.name, { permanent: true, direction: 'right' }).addTo(this.layer);
      const [bottom, top] = [lift.line[0], lift.line[lift.line.length - 1]];
      L.circleMarker(bottom, { radius: 5, color: '#a3392c', fillOpacity: 1 }).bindTooltip('Bottom').addTo(this.layer);
      L.circleMarker(top, { radius: 5, color: '#a3392c', fillColor: '#fff', fillOpacity: 1 }).bindTooltip('Top').addTo(this.layer);
      focus.push(...lift.line);
    }
    const bounds = focus.length ? focus : lifts.flatMap((l) => l.line);
    if (bounds.length) {
      this.map.fitBounds(L.latLngBounds(bounds), { padding: [30, 30], maxZoom: 15 });
    }
  }
}
