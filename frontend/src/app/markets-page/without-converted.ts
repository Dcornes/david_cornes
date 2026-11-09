import { BestPrice, ExchangeQuote, MarketSnapshot } from './market-stream';

/**
 * A snapshot without the exchanges whose prices were converted with an exchange rate,
 * with the best bid/ask, spread and crossed flag worked out again from the rest
 * (same rules as the server: highest bid and lowest ask; on a tie, the larger quantity).
 */
export function withoutConverted(snapshot: MarketSnapshot): MarketSnapshot {
  const exchanges = snapshot.exchanges.filter((q) => !q.converted_from);
  if (exchanges.length === snapshot.exchanges.length) {
    return snapshot;
  }
  if (!exchanges.length) {
    return { ...snapshot, exchanges, best_bid: null, best_ask: null, spread: null, crossed: false };
  }
  const bid = exchanges.reduce((a, b) => (better(b.bid, b.bid_quantity, a.bid, a.bid_quantity, 1) ? b : a));
  const ask = exchanges.reduce((a, b) => (better(b.ask, b.ask_quantity, a.ask, a.ask_quantity, -1) ? b : a));
  return {
    ...snapshot,
    exchanges,
    best_bid: best(bid, bid.bid, bid.bid_quantity),
    best_ask: best(ask, ask.ask, ask.ask_quantity),
    spread: difference(ask.ask, bid.bid),
    crossed: Number(bid.bid) > Number(ask.ask),
  };
}

/** Is price a better than b? direction 1 = higher is better (bids), -1 = lower is better (asks). */
function better(price: string, quantity: string, otherPrice: string, otherQuantity: string, direction: 1 | -1): boolean {
  const diff = (Number(price) - Number(otherPrice)) * direction;
  return diff > 0 || (diff === 0 && Number(quantity) > Number(otherQuantity));
}

function best(quote: ExchangeQuote, price: string, quantity: string): BestPrice {
  return { price, quantity, exchange: quote.exchange };
}

/** a - b as a string, without float noise, using as many decimals as the inputs have. */
function difference(a: string, b: string): string {
  const decimals = Math.max(decimalPlaces(a), decimalPlaces(b));
  return String(Number((Number(a) - Number(b)).toFixed(decimals)));
}

function decimalPlaces(value: string): number {
  return value.includes('.') ? value.split('.')[1].length : 0;
}
