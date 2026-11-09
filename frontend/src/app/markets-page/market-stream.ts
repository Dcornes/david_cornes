import { computed, Injectable, OnDestroy, signal } from '@angular/core';

/** One exchange's latest quote. Numbers are strings so they're never rounded. */
export interface ExchangeQuote {
  exchange: string;
  bid: string;
  bid_quantity: string;
  ask: string;
  ask_quantity: string;
  timestamp: string;
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

/** -1 = last move was down, 1 = up, 0 = no move seen yet. */
export type Direction = -1 | 0 | 1;

export interface PriceMoves {
  bid: Direction;
  ask: Direction;
}

export type StreamStatus = 'connecting' | 'live' | 'reconnecting';

/** Live market snapshots from the backend's Server-Sent Events stream. Provide it per page. */
@Injectable()
export class MarketStream implements OnDestroy {
  private readonly snapshotsBySymbol = signal<Record<string, MarketSnapshot>>({});
  /** symbol -> exchange -> direction of the last bid/ask price change */
  private readonly movesBySymbol = signal<Record<string, Record<string, PriceMoves>>>({});
  private source?: EventSource;

  readonly status = signal<StreamStatus>('connecting');
  readonly snapshots = computed(() =>
    Object.values(this.snapshotsBySymbol()).sort((a, b) => a.symbol.localeCompare(b.symbol)),
  );
  readonly crossed = computed(() => this.snapshots().filter((s) => s.crossed));
  readonly moves = this.movesBySymbol.asReadonly();

  connect(url = '/api/quotes/stream'): void {
    this.source?.close();
    this.status.set('connecting');
    this.source = new EventSource(url);
    this.source.onopen = () => this.status.set('live');
    // EventSource reconnects by itself; just show that it's trying.
    this.source.onerror = () => this.status.set('reconnecting');
    this.source.onmessage = (event: MessageEvent<string>) => this.receive(JSON.parse(event.data));
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
