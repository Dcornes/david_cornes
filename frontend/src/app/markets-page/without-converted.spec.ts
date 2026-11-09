import { ExchangeQuote, MarketSnapshot } from './market-stream';
import { withoutConverted } from './without-converted';

function quote(exchange: string, bid: string, ask: string, extra: Partial<ExchangeQuote> = {}): ExchangeQuote {
  return { exchange, bid, bid_quantity: '1', ask, ask_quantity: '1', timestamp: '2026-11-09T21:00:00+00:00', ...extra };
}

// Bitkub (converted) has the best bid, and makes the market look crossed.
const SNAPSHOT: MarketSnapshot = {
  symbol: 'BTC-USDT',
  exchanges: [
    quote('Bitkub', '74920.68', '74950.1', { converted_from: 'THB' }),
    quote('Kraken', '74909.9', '74916.7'),
    quote('OKX', '74909', '74909.1', { converted_from: null }),
  ],
  best_bid: { price: '74920.68', quantity: '1', exchange: 'Bitkub' },
  best_ask: { price: '74909.1', quantity: '1', exchange: 'OKX' },
  spread: '-11.58',
  crossed: true,
};

describe('withoutConverted', () => {
  it('drops converted exchanges and works out the best prices again', () => {
    const result = withoutConverted(SNAPSHOT);

    expect(result.exchanges.map((q) => q.exchange)).toEqual(['Kraken', 'OKX']);
    expect(result.best_bid).toEqual({ price: '74909.9', quantity: '1', exchange: 'Kraken' });
    expect(result.best_ask).toEqual({ price: '74909.1', quantity: '1', exchange: 'OKX' });
    expect(result.spread).toBe('-0.8');
    expect(result.crossed).toBe(true); // Kraken's bid is still above OKX's ask
  });

  it('prefers the larger quantity on a price tie, like the server', () => {
    const result = withoutConverted({
      ...SNAPSHOT,
      exchanges: [quote('Kraken', '100', '101'), quote('OKX', '100', '101', { bid_quantity: '5', ask_quantity: '7' }), quote('Upbit', '102', '99', { converted_from: 'KRW' })],
    });
    expect(result.best_bid?.exchange).toBe('OKX');
    expect(result.best_ask?.exchange).toBe('OKX');
    expect(result.spread).toBe('1');
    expect(result.crossed).toBe(false);
  });

  it('returns the same snapshot when nothing was converted', () => {
    const plain = { ...SNAPSHOT, exchanges: SNAPSHOT.exchanges.slice(1) };
    expect(withoutConverted(plain)).toBe(plain);
  });

  it('has no best prices when every exchange was converted', () => {
    const result = withoutConverted({ ...SNAPSHOT, exchanges: SNAPSHOT.exchanges.slice(0, 1) });
    expect([result.exchanges.length, result.best_bid, result.best_ask, result.spread, result.crossed]).toEqual([0, null, null, null, false]);
  });
});
