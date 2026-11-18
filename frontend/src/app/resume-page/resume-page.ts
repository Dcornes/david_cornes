import { Component } from '@angular/core';
import { SiteNav } from '../site-nav/site-nav';
import { RESUME } from '../resume';

@Component({
  imports: [SiteNav],
  selector: 'app-resume-page',
  styleUrl: './resume-page.css',
  templateUrl: './resume-page.html',
})
export class ResumePage {
  protected readonly resume = RESUME;
  protected readonly year = new Date().getFullYear();
  protected readonly phoneHref = 'tel:+1' + RESUME.phone.replace(/\D/g, '');
}
