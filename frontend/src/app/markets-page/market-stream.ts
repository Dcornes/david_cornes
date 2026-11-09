import { computed, Injectable, OnDestroy, signal } from '@angular/core';

/** One exchange's latest quote. Numbers are strings so they're never rounded. */
export interface ExchangeQuote {
  exchange: string;
  bid: string;
  bid_quantity: string;
  ask: string;
  ask_quantity: string;
  timestamp: string;
  /** Set when the prices were converted with an exchange rate, e.g. "THB". */
  converted_from?: string | null;
}

export interface BestPrice {
  price: string;
  quantity: string;
  exchange: string;
}

/** Sent by /api/quotes/stream whenever a price changes for a pair. */
export interface MarketSnapshot {
  symbol: string;
  exchanges: ExchangeQuote[];
  best_bid: BestPrice | null;
  best_ask: BestPrice | null;
  spread: string | null;
  crossed: boolean;
}

/** Sent as an `update` event after the first snapshot: only what changed in a pair. */
export interface MarketUpdate {
  symbol: string;
  changed: ExchangeQuote[]; // exchanges whose quote changed (or that are new)
  removed: string[]; // exchanges that dropped out
  best_bid: BestPrice | null;
  best_ask: BestPrice | null;
  spread: string | null;
  crossed: boolean;
}

/** -1 = last move was down, 1 = up, 0 = no move seen yet. */
export type Direction = -1 | 0 | 1;

export interface PriceMoves {
  bid: Direction;
  ask: Direction;
}

export type StreamStatus = 'connecting' | 'live' | 'reconnecting';

export type FeedState = 'connecting' | 'live' | 'reconnecting' | 'stopped';

/** Health of one exchange's feed on the server, sent as a `status` event. */
export interface FeedStatus {
  exchange: string;
  state: FeedState;
  message: string; // why it stopped, for reconnecting / stopped
  since: string; // when it entered this state
}

/** Live market snapshots from the backend's Server-Sent Events stream. Provide it per page. */
@Injectable()
export class MarketStream implements OnDestroy {
  private readonly snapshotsBySymbol = signal<Record<string, MarketSnapshot>>({});
  /** symbol -> exchange -> direction of the last bid/ask price change */
  private readonly movesBySymbol = signal<Record<string, Record<string, PriceMoves>>>({});
  /** exchange -> time of its newest quote (ms since epoch) */
  private readonly lastQuoteAt = signal<Record<string, number>>({});
  private source?: EventSource;

  readonly status = signal<StreamStatus>('connecting');
  readonly snapshots = computed(() =>
    Object.values(this.snapshotsBySymbol()).sort((a, b) => a.symbol.localeCompare(b.symbol)),
  );
  readonly crossed = computed(() => this.snapshots().filter((s) => s.crossed));
  readonly moves = this.movesBySymbol.asReadonly();
  readonly feeds = signal<FeedStatus[]>([]);
  readonly lastUpdates = this.lastQuoteAt.asReadonly();

  connect(url = '/api/quotes/stream'): void {
    this.source?.close();
    this.status.set('connecting');
    this.source = new EventSource(url);
    this.source.onopen = () => this.status.set('live');
    // EventSource reconnects by itself; just show that it's trying.
    this.source.onerror = () => this.status.set('reconnecting');
    this.source.onmessage = (event: MessageEvent<string>) => this.receive(JSON.parse(event.data));
    this.source.addEventListener('update', (event) => this.applyUpdate(JSON.parse((event as MessageEvent<string>).data)));
    this.source.addEventListener('status',(event) => this.feeds.set(JSON.parse((event as MessageEvent<string>).data)));
  }

  /** Merge an update into the pair's last snapshot. The server always sends a full snapshot first. */
  applyUpdate(update: MarketUpdate): void {
    const previous = this.snapshotsBySymbol()[update.symbol];
    if (!previous) {
      return;
    }
    const quotes = new Map(previous.exchanges.map((q) => [q.exchange, q]));
    for (const exchange of update.removed) {
      quotes.delete(exchange);
    }
    for (const quote of update.changed) {
      quotes.set(quote.exchange, quote);
    }
    const { changed, removed, ...prices } = update;
    // Same order as the server's snapshots: by exchange name, ignoring case.
    const exchanges = [...quotes.values()].sort((a, b) => (a.exchange.toLowerCase() < b.exchange.toLowerCase() ? -1 : 1));
    this.receive({ ...prices, exchanges });
  }

  receive(snapshot: MarketSnapshot): void {
    const previous = this.snapshotsBySymbol()[snapshot.symbol];
    const oldMoves = this.movesBySymbol()[snapshot.symbol] ?? {};
    const newMoves: Record<string, PriceMoves> = {};
    for (const quote of snapshot.exchanges) {
      const before = previous?.exchanges.find((q) => q.exchange === quote.exchange);
      const kept = oldMoves[quote.exchange] ?? { bid: 0, ask: 0 };
      newMoves[quote.exchange] = {
        bid: direction(before?.bid, quote.bid, kept.bid),
        ask: direction(before?.ask, quote.ask, kept.ask),
      };
    }
    this.snapshotsBySymbol.update((all) => ({ ...all, [snapshot.symbol]: snapshot }));
    this.movesBySymbol.update((all) => ({ ...all, [snapshot.symbol]: newMoves }));
    this.lastQuoteAt.update((all) => {
      const next = { ...all };
      for (const quote of snapshot.exchanges) {
        next[quote.exchange] = Math.max(next[quote.exchange] ?? 0, Date.parse(quote.timestamp));
      }
      return next;
    });
  }

  ngOnDestroy(): void {
    this.source?.close();
  }
}

/** Which way a price moved; keeps the last direction when it hasn't changed. */
function direction(before: string | undefined, after: string, last: Direction): Direction {
  if (before === undefined || Number(before) === Number(after)) {
    return last;
  }
  return Number(after) > Number(before) ? 1 : -1;
}
