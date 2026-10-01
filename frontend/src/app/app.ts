import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ThemeService } from './core/services/theme.service';
import { NAV_SECTIONS } from './core/nav';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  styleUrl: './app.scss',
  templateUrl: './app.html',
})
export class App {
  readonly theme = inject(ThemeService);
  readonly sections = NAV_SECTIONS;
}
