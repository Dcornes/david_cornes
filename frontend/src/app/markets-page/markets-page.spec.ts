import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { FeedStatus, MarketSnapshot } from './market-stream';
import { MarketsPage } from './markets-page';

/** Stands in for the browser's EventSource so tests can push messages. */
class FakeEventSource {
  static last: FakeEventSource;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  closed = false;
  private readonly listeners: Record<string, ((event: MessageEvent<string>) => void)[]> = {};

  constructor(readonly url: string) {
    FakeEventSource.last = this;
  }

  open(): void {
    this.onopen?.();
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
    (this.listeners[type] ??= []).push(listener);
  }

  sendStatus(feeds: FeedStatus[]): void {
    this.sendEvent('status', feeds);
  }

  sendEvent(type: string, data: unknown): void {
    for (const listener of this.listeners[type] ?? []) {
      listener({ data: JSON.stringify(data) } as MessageEvent<string>);
    }
  }

  send(snapshot: MarketSnapshot): void {
    this.onmessage?.({ data: JSON.stringify(snapshot) } as MessageEvent<string>);
  }

  close(): void {
    this.closed = true;
  }
}

function quote(exchange: string, bid: string, ask: string) {
  return { exchange, bid, bid_quantity: '1', ask, ask_quantity: '2', timestamp: '2026-11-09T17:00:42.608000+00:00' };
}

const BTC: MarketSnapshot = {
  symbol: 'BTC-USDT',
  exchanges: [quote('Kraken', '75012.1', '75017.6'), quote('OKX', '75022.2', '75022.3')],
  best_bid: { price: '75022.2', quantity: '1', exchange: 'OKX' },
  best_ask: { price: '75017.6', quantity: '2', exchange: 'Kraken' },
  spread: '-4.6',
  crossed: true,
};

const ETH: MarketSnapshot = {
  symbol: 'ETH-USDT',
  exchanges: [quote('OKX', '2130.87', '2130.88')],
  best_bid: { price: '2130.87', quantity: '1', exchange: 'OKX' },
  best_ask: { price: '2130.88', quantity: '2', exchange: 'OKX' },
  spread: '0.01',
  crossed: false,
};

describe('MarketsPage', () => {
  let fixture: ComponentFixture<MarketsPage>;
  let page: HTMLElement;

  function push(snapshot: MarketSnapshot): void {
    FakeEventSource.last.send(snapshot);
    fixture.detectChanges();
  }

  beforeEach(async () => {
    vi.stubGlobal('EventSource', FakeEventSource);
    await TestBed.configureTestingModule({ imports: [MarketsPage], providers: [provideRouter([])] }).compileComponents();
    fixture = TestBed.createComponent(MarketsPage);
    fixture.detectChanges();
    page = fixture.nativeElement as HTMLElement;
  });

  afterEach(() => vi.unstubAllGlobals());

  it('connects to the quote stream and shows its status', () => {
    expect(FakeEventSource.last.url).toBe('/api/quotes/stream');
    expect(page.querySelector('.status')?.textContent).toContain('Connecting');

    FakeEventSource.last.open();
    fixture.detectChanges();
    expect(page.querySelector('.status')?.textContent).toContain('Connected');

    FakeEventSource.last.onerror?.();
    fixture.detectChanges();
    expect(page.querySelector('.status')?.textContent).toContain('Reconnecting');
  });

  it('shows each exchange with the best bid and ask highlighted', () => {
    push(ETH);
    push(BTC);

    const markets = page.querySelectorAll('.market');
    expect([...markets].map((m) => m.querySelector('h2')?.textContent?.trim().split(/\s/)[0])).toEqual([
      'BTC-USDT',
      'ETH-USDT',
    ]);

    const btc = markets[0];
    const rows = btc.querySelectorAll('tbody tr');
    expect([...rows].map((r) => r.querySelector('th')?.textContent)).toEqual(['Kraken', 'OKX']);
    expect(btc.querySelector('td.best')?.textContent?.trim()).toBe('75017.6'); // Kraken's ask comes first
    expect([...btc.querySelectorAll('td.best')].map((td) => td.textContent?.trim())).toEqual(['75017.6', '75022.2']);
    expect(btc.querySelector('.overview')?.textContent).toContain('75022.2');
    expect(btc.querySelector('.tag')?.classList).not.toContain('hidden');
    expect(markets[1].querySelector('.tag')?.classList).toContain('hidden'); // ETH isn't crossed
  });

  it('lists every pair in crossed markets, with the gap when crossed', () => {
    push(ETH);
    expect(page.querySelector('.crossed li')?.textContent).toContain('Not crossed');

    push(BTC);
    const [btc, eth] = page.querySelectorAll('.crossed li');
    expect(btc.classList).toContain('is-crossed');
    expect(btc.textContent).toContain('BTC-USDT');
    expect(btc.textContent?.replace(/\s+/g, ' ')).toContain('bid 75022.2 on OKX is above ask 75017.6 on Kraken');
    expect(btc.querySelector('.gap')?.textContent).toBe('+4.6');
    expect(eth.classList).not.toContain('is-crossed');
    expect(eth.textContent).toContain('Not crossed');
  });

  it('uses fixed column widths so tables keep their size', () => {
    push(BTC);
    expect(page.querySelectorAll('.market colgroup col').length).toBe(6);
  });

  it('updates prices and colours their last move', () => {
    push(ETH);
    push({ ...ETH, exchanges: [quote('OKX', '2131.00', '2130.50')] });

    const [bid, ask] = page.querySelectorAll('.market td.price');
    expect(bid.textContent?.trim()).toBe('2131.00');
    expect(bid.classList).toContain('up');
    expect(ask.classList).toContain('down');
  });

  it('closes the stream when leaving the page', () => {
    const source = FakeEventSource.last;
    fixture.destroy();
    expect(source.closed).toBe(true);
  });

  it('shows each exchange feed status at the bottom of the page', () => {
    FakeEventSource.last.open();
    FakeEventSource.last.sendStatus([
      feed('OKX', 'live'),
      feed('Bitstamp', 'reconnecting', 'connection dropped'),
      feed('Kraken', 'connecting'),
    ]);
    push({ ...ETH, exchanges: [{ ...quote('OKX', '1', '2'), timestamp: new Date().toISOString() }] });

    const health = page.querySelector('.health')!;
    expect(health.compareDocumentPosition(page.querySelector('.market')!)).toBe(Node.DOCUMENT_POSITION_PRECEDING);
    const rows = [...health.querySelectorAll('.feeds li')];
    expect(rows.map((r) => [r.querySelector('.name')?.textContent, r.className])).toEqual([
      ['OKX', 'live'],
      ['Bitstamp', 'reconnecting'],
      ['Kraken', 'connecting'],
    ]);
    expect(rows[0].textContent).toContain('Last update');
    expect(rows[1].textContent).toContain('connection dropped');
    expect(rows[1].querySelector('.state')?.textContent).toBe('Reconnecting');
  });

  it('applies updates that carry only the exchanges that changed', () => {
    push(BTC);
    FakeEventSource.last.sendEvent('update', {
      symbol: 'BTC-USDT',
      changed: [quote('OKX', '75030.0', '75030.1')],
      removed: ['Kraken'],
      best_bid: { price: '75030.0', quantity: '1', exchange: 'OKX' },
      best_ask: { price: '75030.1', quantity: '2', exchange: 'OKX' },
      spread: '0.1',
      crossed: false,
    });
    FakeEventSource.last.sendEvent('update', {
      symbol: 'BTC-USDT',
      changed: [quote('Bitstamp', '75029.0', '75031.0')],
      removed: [],
      best_bid: { price: '75030.0', quantity: '1', exchange: 'OKX' },
      best_ask: { price: '75030.1', quantity: '2', exchange: 'OKX' },
      spread: '0.1',
      crossed: false,
    });
    fixture.detectChanges();

    const market = page.querySelector('.market')!;
    const rows = [...market.querySelectorAll('tbody tr')].map((r) => r.querySelector('th')?.textContent);
    expect(rows).toEqual(['Bitstamp', 'OKX']); // Kraken removed, Bitstamp added, still sorted
    expect(market.querySelector('td.best')?.textContent?.trim()).toBe('75030.0');
    expect(market.querySelector('.overview')?.textContent).toContain('0.1');
    expect(market.querySelectorAll('.price.up').length).toBeGreaterThan(0); // OKX moved up
  });

  it('ignores an update for a pair it has no snapshot for', () => {
    FakeEventSource.last.sendEvent('update', { symbol: 'XRP-USD', changed: [], removed: [], best_bid: null, best_ask: null, spread: null, crossed: false });
    fixture.detectChanges();
    expect(page.querySelectorAll('.market').length).toBe(0);
  });

  it('marks a live feed quiet when its last quote is over a minute old', () => {
    FakeEventSource.last.open();
    FakeEventSource.last.sendStatus([feed('OKX', 'live')]);
    push({ ...ETH, exchanges: [{ ...quote('OKX', '1', '2'), timestamp: new Date(Date.now() - 120_000).toISOString() }] });

    const row = page.querySelector('.feeds li')!;
    expect(row.className).toBe('quiet');
    expect(row.querySelector('.state')?.textContent).toBe('No recent updates');
  });

  it('shows feed status as unknown while disconnected from the server', () => {
    FakeEventSource.last.open();
    FakeEventSource.last.sendStatus([feed('OKX', 'live')]);
    FakeEventSource.last.onerror?.();
    fixture.detectChanges();

    expect(page.querySelector('.feeds li')?.className).toBe('unknown');
  });
});

function feed(exchange: string, state: FeedStatus['state'], message = ''): FeedStatus {
  return { exchange, state, message, since: '2026-11-09T19:00:00+00:00' };
}
