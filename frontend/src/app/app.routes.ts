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
  {
    path: 'ski',
    loadComponent: () => import('./ski-page/ski-page').then((m) => m.SkiPage),
    title: 'Skiing · David Cornes',
  },
  {
    path: 'ski/:resort',
    loadComponent: () => import('./ski-page/ski-page').then((m) => m.SkiPage),
    title: 'Skiing · David Cornes',
  },
  // The skiing pages were merged into /ski; old links still work.
  { path: 'snow', redirectTo: 'ski' },
  { path: 'lifts', redirectTo: 'ski' },
  { path: 'heatmap', redirectTo: 'ski/big-sky' },
  { path: '**', redirectTo: '' },
];
