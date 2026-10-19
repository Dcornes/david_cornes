import { Component } from '@angular/core';
import { RESUME } from './resume';

@Component({
  imports: [],
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
})
export class App {
  protected readonly resume = RESUME;
  protected readonly year = new Date().getFullYear();
  protected readonly phoneHref = 'tel:+1' + RESUME.phone.replace(/\D/g, '');
}
