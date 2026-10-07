import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { App } from './app';

describe('App', () => {
  let httpTesting: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpTesting.verify());

  it('embeds the résumé PDF', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    httpTesting.expectOne('/api/health').flush({ status: 'ok' });

    const embed = (fixture.nativeElement as HTMLElement).querySelector('object.resume');
    expect(embed?.getAttribute('data')).toBe('/resume.pdf');
    expect(embed?.getAttribute('type')).toBe('application/pdf');
  });

  it('shows the backend status', async () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();

    httpTesting.expectOne('/api/health').flush({ status: 'ok' });
    await fixture.whenStable();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.up')?.textContent).toContain('ok');
  });

  it('shows unreachable when the backend fails', async () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();

    httpTesting
      .expectOne('/api/health')
      .flush('down', { status: 502, statusText: 'Bad Gateway' });
    await fixture.whenStable();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.down')?.textContent).toContain('unreachable');
  });
});
