import { DatePipe } from '@angular/common';
import { Component, computed, DestroyRef, inject, OnInit, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Direction, FeedState, MarketSnapshot, MarketStream } from './market-stream';
import { TimeZonePipe } from './time-zone.pipe';
import { withoutConverted } from './without-converted';

/** A live feed with no new quote for this long is shown as quiet. */
const QUIET_AFTER_MS = 60_000;

/** Where the viewer's "show converted prices" choice is remembered (this browser only). */
const SHOW_CONVERTED_KEY = 'markets.showConverted';

/** quiet: connected but no recent quotes; unknown: we've lost our own connection to the server. */
type FeedView = FeedState | 'quiet' | 'unknown';

const FEED_LABELS: Record<FeedView, string> = {
  connecting: 'Connecting…',
  live: 'Live',
  quiet: 'No recent updates',
  reconnecting: 'Reconnecting',
  stopped: 'Stopped',
  unknown: 'Unknown',
};

@Component({
  imports: [DatePipe, RouterLink, TimeZonePipe],
  providers: [MarketStream],
  selector: 'app-markets-page',
  styleUrl: './markets-page.css',
  templateUrl: './markets-page.html',
})
export class MarketsPage implements OnInit {
  protected readonly market = inject(MarketStream);

  /** Whether to include exchanges whose prices were converted from THB/KRW with an exchange rate. */
  protected readonly showConverted = signal(loadShowConverted());

  /** Snapshots as shown: without converted exchanges (and with best prices recalculated) when hidden. */
  protected readonly snapshots = computed(() =>
    this.showConverted() ? this.market.snapshots() : this.market.snapshots().map(withoutConverted),
  );

  /** Ticks every few seconds so a feed that goes quiet is noticed. */
  private readonly now = signal(Date.now());

  /** One row per exchange for the status section. */
  protected readonly feedRows = computed(() => {
    const connected = this.market.status() === 'live';
    const now = this.now();
    return this.market.feeds().map((feed) => {
      const lastUpdate = this.market.lastUpdates()[feed.exchange];
      let view: FeedView = connected ? feed.state : 'unknown';
      if (view === 'live' && lastUpdate !== undefined && now - lastUpdate > QUIET_AFTER_MS) {
        view = 'quiet';
      }
      return { ...feed, view, label: FEED_LABELS[view], lastUpdate };
    });
  });

  constructor() {
    const timer = setInterval(() => this.now.set(Date.now()), 5_000);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  ngOnInit(): void {
    this.market.connect();
  }

  /** How far one exchange's bid is above another's ask. */
  protected gap(snapshot: MarketSnapshot): string {
    if (!snapshot.best_bid || !snapshot.best_ask) {
      return '';
    }
    return formatDifference(snapshot.best_bid.price, snapshot.best_ask.price);
  }

  protected setShowConverted(show: boolean): void {
    this.showConverted.set(show);
    try {
      localStorage.setItem(SHOW_CONVERTED_KEY, String(show));
    } catch {
      // Storage can be unavailable (private mode, blocked); the choice just isn't remembered.
    }
  }

  /** USDT in BTC-USDT. */
  protected quoteOf(symbol: string): string {
    return symbol.split('-')[1] ?? '';
  }

  protected moveClass(direction: Direction | undefined): string {
    return direction === 1 ? 'up' : direction === -1 ? 'down' : '';
  }
}

function loadShowConverted(): boolean {
  try {
    return localStorage.getItem(SHOW_CONVERTED_KEY) !== 'false';
  } catch {
    return true;
  }
}

/** a - b without float noise, using as many decimals as the inputs have. */
function formatDifference(a: string, b: string): string {
  const decimals = Math.max(decimalPlaces(a), decimalPlaces(b));
  return (Number(a) - Number(b)).toFixed(decimals);
}

function decimalPlaces(value: string): number {
  return value.includes('.') ? value.split('.')[1].length : 0;
}
