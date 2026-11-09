import { DatePipe } from '@angular/common';
import { Component, computed, DestroyRef, inject, OnInit, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Direction, FeedState, MarketSnapshot, MarketStream } from './market-stream';
import { TimeZonePipe } from './time-zone.pipe';

/** A live feed with no new quote for this long is shown as quiet. */
const QUIET_AFTER_MS = 60_000;

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

  protected moveClass(direction: Direction | undefined): string {
    return direction === 1 ? 'up' : direction === -1 ? 'down' : '';
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
