import { DatePipe, NgTemplateOutlet } from '@angular/common';
import { Component, DestroyRef, inject, OnInit, signal } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { RouterLink } from '@angular/router';
import { TimeZonePipe } from '../markets-page/time-zone.pipe';
import { SiteNav } from '../site-nav/site-nav';

export interface Stat {
  label: string;
  value: string;
  detail: string | null;
}

export interface Table {
  title: string;
  columns: string[];
  rows: string[][];
  note: string | null;
}

export interface Webcam {
  name: string;
  /** image: a JPEG replaced every few minutes; embed: a live player shown in an iframe */
  kind: 'image' | 'embed';
  src: string;
  link: string;
  updated: string | null;
}

/** One ski area, already formatted for display by the server. */
export interface Resort {
  id: string;
  name: string;
  website: string;
  source: string;
  source_url: string;
  updated: string | null;
  summary: Stat[];
  webcams: Webcam[];
  featured_webcam: number | null;
  tables: Table[];
  message: string | null;
  webcams_page: string | null;
  fetched_at: string;
  /** The last refresh failed; this is the previous result. */
  stale: boolean;
}

/** What /api/snow returns (refreshed hourly on the server). */
export interface SnowData {
  fetched_at: string;
  resorts: Resort[];
}

/** The server only fetches hourly; checking every 10 minutes picks that up soon enough. */
const REFRESH_MS = 10 * 60_000;
/** Before the server's first fetch (just after it starts) it answers 503; try again soon. */
const RETRY_MS = 15_000;
/** Webcam images older than this are marked as not updating. */
const STALE_WEBCAM_MS = 24 * 60 * 60_000;
/** Webcam images change every few minutes; one URL per 5 minutes lets browsers cache them in between. */
const WEBCAM_CACHE_MS = 5 * 60_000;
/** Which resorts the viewer opened (this browser only). All start collapsed. */
const OPENED_KEY = 'snow.opened';

@Component({
  imports: [DatePipe, NgTemplateOutlet, RouterLink, SiteNav, TimeZonePipe],
  selector: 'app-snow-page',
  styleUrl: './snow-page.css',
  templateUrl: './snow-page.html',
})
export class SnowPage implements OnInit {
  private readonly sanitizer = inject(DomSanitizer);
  protected readonly data = signal<SnowData | null>(null);
  protected readonly error = signal<string | null>(null);
  /** Resort ids the viewer opened; the rest are collapsed (showing only a thumbnail of their featured webcam). */
  protected readonly opened = signal<Set<string>>(loadOpened());
  /** Resort ids whose "more" section is open. Its webcams load only then. */
  protected readonly expanded = signal<Set<string>>(new Set());
  private readonly now = signal(Date.now());
  private readonly embeds = new Map<string, SafeResourceUrl>();
  private timer?: ReturnType<typeof setTimeout>;

  constructor() {
    inject(DestroyRef).onDestroy(() => clearTimeout(this.timer));
  }

  ngOnInit(): void {
    void this.load();
  }

  private async load(): Promise<void> {
    let next = REFRESH_MS;
    try {
      const response = await fetch('/api/snow');
      if (response.status === 503) {
        this.error.set(this.data() ? null : 'Fetching the latest snow totals…');
        next = RETRY_MS;
      } else if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      } else {
        this.data.set(await response.json());
        this.error.set(null);
      }
    } catch {
      if (!this.data()) {
        this.error.set("Couldn't load snow totals. Trying again shortly.");
      }
      next = RETRY_MS;
    }
    this.now.set(Date.now());
    this.timer = setTimeout(() => void this.load(), next);
  }

  protected isOpen(resort: Resort): boolean {
    return this.opened().has(resort.id);
  }

  protected setOpen(resort: Resort, open: boolean): void {
    const next = new Set(this.opened());
    if (open) {
      next.add(resort.id);
    } else {
      next.delete(resort.id);
    }
    this.opened.set(next);
    try {
      localStorage.setItem(OPENED_KEY, JSON.stringify([...next]));
    } catch {
      // Storage can be unavailable (private mode, blocked); the choice just isn't remembered.
    }
  }

  protected isExpanded(resort: Resort): boolean {
    return this.expanded().has(resort.id);
  }

  protected setExpanded(resort: Resort, open: boolean): void {
    const next = new Set(this.expanded());
    if (open) {
      next.add(resort.id);
    } else {
      next.delete(resort.id);
    }
    this.expanded.set(next);
  }

  protected featured(resort: Resort): Webcam | null {
    return resort.featured_webcam === null ? null : (resort.webcams[resort.featured_webcam] ?? null);
  }

  protected otherWebcams(resort: Resort): Webcam[] {
    return resort.webcams.filter((_, i) => i !== resort.featured_webcam);
  }

  protected imageUrl(cam: Webcam): string {
    const version = Math.floor(this.now() / WEBCAM_CACHE_MS);
    return `${cam.src}${cam.src.includes('?') ? '&' : '?'}v=${version}`;
  }

  /** Live players come from the server's list of the resorts' own embeds. */
  protected embedUrl(cam: Webcam): SafeResourceUrl {
    let url = this.embeds.get(cam.src);
    if (!url) {
      url = this.sanitizer.bypassSecurityTrustResourceUrl(cam.src);
      this.embeds.set(cam.src, url);
    }
    return url;
  }

  protected isStaleWebcam(cam: Webcam): boolean {
    return !!cam.updated && this.now() - Date.parse(cam.updated) > STALE_WEBCAM_MS;
  }
}

function loadOpened(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(OPENED_KEY) ?? '[]'));
  } catch {
    return new Set();
  }
}
