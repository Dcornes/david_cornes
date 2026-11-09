import { DatePipe } from '@angular/common';
import { Component, inject, OnInit } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Direction, MarketSnapshot, MarketStream } from './market-stream';

@Component({
  imports: [DatePipe, RouterLink],
  providers: [MarketStream],
  selector: 'app-markets-page',
  styleUrl: './markets-page.css',
  templateUrl: './markets-page.html',
})
export class MarketsPage implements OnInit {
  protected readonly market = inject(MarketStream);

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
