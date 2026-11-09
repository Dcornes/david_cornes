import { Routes } from '@angular/router';
import { ResumePage } from './resume-page/resume-page';

export const routes: Routes = [
  { path: '', component: ResumePage, title: 'David Cornes' },
  {
    path: 'markets',
    // Loaded only when visited, so the résumé page stays small.
    loadComponent: () => import('./markets-page/markets-page').then((m) => m.MarketsPage),
    title: 'Live markets · David Cornes',
  },
  { path: '**', redirectTo: '' },
];
