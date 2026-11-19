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
    path: 'snow',
    loadComponent: () => import('./snow-page/snow-page').then((m) => m.SnowPage),
    title: 'Snow report · David Cornes',
  },
  {
    path: 'lifts',
    loadComponent: () => import('./lifts-page/lifts-page').then((m) => m.LiftsPage),
    title: 'Ski lift vertical · David Cornes',
  },
  {
    path: 'heatmap',
    loadComponent: () => import('./heatmap-page/heatmap-page').then((m) => m.HeatmapPage),
    title: 'Snow heatmap · David Cornes',
  },
  { path: '**', redirectTo: '' },
];
