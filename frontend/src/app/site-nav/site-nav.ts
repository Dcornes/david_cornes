import { Component, ElementRef, inject, signal } from '@angular/core';
import { NavigationEnd, Router, RouterLink, RouterLinkActive } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { filter } from 'rxjs';

/**
 * The site's navigation, at the top right of every page: Home, and a Projects menu with the
 * project pages. The current page is underlined (Projects too, on a project page). Extra
 * links can be projected after them.
 */
@Component({
  imports: [RouterLink, RouterLinkActive],
  selector: 'app-site-nav',
  host: {
    '(document:click)': 'onDocumentClick($event)',
    '(document:keydown.escape)': 'close()',
  },
  styles: `
    nav {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      align-items: baseline;
      gap: 0.25rem 1.5rem;
    }

    nav ::ng-deep a,
    .menu-button {
      font-size: 1.125rem;
      font-weight: 600;
      transition: opacity 0.15s;
    }

    nav ::ng-deep a:hover,
    .menu-button:hover {
      opacity: 0.6;
    }

    .current,
    .menu-button.current {
      text-decoration: underline;
      text-decoration-thickness: 2px;
      text-underline-offset: 6px;
    }

    .projects {
      position: relative;
    }

    .menu-button {
      font-family: inherit;
      color: inherit;
      background: none;
      border: 0;
      padding: 0;
      cursor: pointer;
    }

    .caret {
      display: inline-block;
      margin-left: 0.3rem;
      font-size: 0.75em;
      transition: transform 0.15s;
    }

    .open .caret {
      transform: rotate(180deg);
    }

    .menu {
      position: absolute;
      right: 0;
      top: calc(100% + 0.6rem);
      z-index: 1000; /* above the Leaflet map */
      min-width: 12rem;
      margin: 0;
      padding: 0.4rem 0;
      list-style: none;
      background: var(--bg);
      border: 1px solid var(--line);
      box-shadow: 0 6px 18px rgb(0 0 0 / 0.12);
    }

    .menu a {
      display: block;
      padding: 0.45rem 1rem;
      white-space: nowrap;
    }

    .menu a.current {
      text-underline-offset: 4px;
    }

    @media print {
      :host {
        display: none;
      }
    }
  `,
  template: `
    <nav aria-label="Primary">
      <a routerLink="/" routerLinkActive="current" ariaCurrentWhenActive="page" [routerLinkActiveOptions]="{ exact: true }"
        >Home</a
      >
      <div class="projects" [class.open]="open()">
        <button
          type="button"
          class="menu-button"
          [class.current]="onProject()"
          aria-haspopup="true"
          [attr.aria-expanded]="open()"
          aria-controls="projects-menu"
          (click)="toggle()"
        >
          Projects<span class="caret" aria-hidden="true">▾</span>
        </button>
        @if (open()) {
          <ul id="projects-menu" class="menu">
            @for (project of projects; track project.path) {
              <li>
                <a [routerLink]="project.path" routerLinkActive="current" ariaCurrentWhenActive="page" (click)="close()">{{
                  project.label
                }}</a>
              </li>
            }
          </ul>
        }
      </div>
      <ng-content />
    </nav>
  `,
})
export class SiteNav {
  protected readonly projects = [
    { path: '/markets', label: 'Live markets' },
    { path: '/ski', label: 'Skiing' },
  ];
  protected readonly open = signal(false);
  protected readonly onProject = signal(false);
  private readonly router = inject(Router);
  private readonly element = inject(ElementRef<HTMLElement>);

  constructor() {
    this.updateCurrent();
    this.router.events
      .pipe(
        filter((e) => e instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe(() => {
        this.updateCurrent();
        this.close();
      });
  }

  protected toggle(): void {
    this.open.update((open) => !open);
  }

  protected close(): void {
    this.open.set(false);
  }

  /** Close the menu on a click anywhere outside it. */
  protected onDocumentClick(event: MouseEvent): void {
    const projects = (this.element.nativeElement as HTMLElement).querySelector('.projects');
    if (this.open() && projects && !projects.contains(event.target as Node)) {
      this.close();
    }
  }

  private updateCurrent(): void {
    const path = this.router.url.split(/[?#]/)[0];
    this.onProject.set(this.projects.some((p) => path === p.path || path.startsWith(p.path + '/')));
  }
}
