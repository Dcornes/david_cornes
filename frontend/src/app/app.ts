import { Component } from '@angular/core';
import { httpResource } from '@angular/common/http';

interface Health {
  status: string;
}

@Component({
  imports: [],
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
})
export class App {
  protected readonly health = httpResource<Health>(() => '/api/health');
}
