import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ResumePage } from './resume-page';
import { RESUME } from '../resume';

describe('ResumePage', () => {
  function render(): HTMLElement {
    const fixture = TestBed.createComponent(ResumePage);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [ResumePage], providers: [provideRouter([])] }).compileComponents();
  });

  it('renders the name, title, summary and contact links', () => {
    const page = render();
    expect(page.querySelector('h1')?.textContent).toContain(RESUME.name);
    expect(page.querySelector('h1')?.textContent).toContain(RESUME.title);
    expect(page.querySelector('.summary')?.textContent).toContain(RESUME.summary);
    expect(page.querySelector(`a[href="mailto:${RESUME.email}"]`)).toBeTruthy();
    expect(page.querySelector('a[href="/resume.pdf"]')).toBeTruthy();
    expect(page.querySelector('a[href="tel:+13127713328"]')?.textContent).toContain(RESUME.phone);
    expect(page.querySelector(`a[href="${RESUME.linkedin}"]`)?.getAttribute('target')).toBe('_blank');
  });

  it('renders every job with its bullet points', () => {
    const page = render();
    const jobs = page.querySelectorAll('.job');
    expect(jobs.length).toBe(RESUME.experience.length);

    RESUME.experience.forEach((job, i) => {
      expect(jobs[i].querySelector('h3')?.textContent).toContain(job.title);
      expect(jobs[i].textContent).toContain(job.company);
      expect(jobs[i].querySelectorAll('.bullets li').length).toBe(job.bullets.length);
    });
  });

  it('links to the project pages from the Projects menu', () => {
    const fixture = TestBed.createComponent(ResumePage);
    fixture.detectChanges();
    const page = fixture.nativeElement as HTMLElement;
    const button = page.querySelector<HTMLButtonElement>('.menu-button')!;
    expect(button.textContent).toContain('Projects');
    expect(page.querySelector('a[href="/markets"]')).toBeNull(); // closed by default

    button.click();
    fixture.detectChanges();
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(page.querySelector('a[href="/markets"]')?.textContent).toContain('Live markets');
    expect(page.querySelector('a[href="/ski"]')?.textContent).toContain('Skiing');

    document.body.click(); // a click elsewhere closes it
    fixture.detectChanges();
    expect(page.querySelector('a[href="/markets"]')).toBeNull();
  });

  it('renders skills and education', () => {
    const page = render();
    expect(page.querySelectorAll('.skill').length).toBe(RESUME.skills.length);
    expect(page.querySelector('.skill-items')?.textContent).toContain('Python');
    expect(page.querySelectorAll('.school').length).toBe(RESUME.education.length);
  });
});
