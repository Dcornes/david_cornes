import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/** The site's tabs, shown at the top right of every page; the current one is underlined. Extra links can be projected after them. */
@Component({
  imports: [RouterLink, RouterLinkActive],
  selector: 'app-site-nav',
  styles: `
    nav {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 0.25rem 1.5rem;
    }

    nav ::ng-deep a {
      font-size: 1.125rem;
      font-weight: 600;
      transition: opacity 0.15s;
    }

    nav ::ng-deep a:hover {
      opacity: 0.6;
    }

    .current {
      text-decoration: underline;
      text-decoration-thickness: 2px;
      text-underline-offset: 6px;
    }

    @media print {
      :host {
        display: none;
      }
    }
  `,
  template: `
    <nav aria-label="Primary">
      @for (tab of tabs; track tab.path) {
        <a
          [routerLink]="tab.path"
          routerLinkActive="current"
          ariaCurrentWhenActive="page"
          [routerLinkActiveOptions]="{ exact: true }"
          >{{ tab.label }}</a
        >
      }
      <ng-content />
    </nav>
  `,
})
export class SiteNav {
  protected readonly tabs = [
    { path: '/', label: 'Home' },
    { path: '/markets', label: 'Live markets' },
    { path: '/snow', label: 'Snow' },
    { path: '/lifts', label: 'Ski lift vertical' },
  ];
}
