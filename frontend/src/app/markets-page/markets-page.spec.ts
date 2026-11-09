import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { MarketSnapshot } from './market-stream';
import { MarketsPage } from './markets-page';

/** Stands in for the browser's EventSource so tests can push messages. */
class FakeEventSource {
  static last: FakeEventSource;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    FakeEventSource.last = this;
  }

  open(): void {
    this.onopen?.();
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
    expect(page.querySelector('.status')?.textContent).toContain('Live');

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
});
