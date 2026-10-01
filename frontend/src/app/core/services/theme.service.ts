import { DOCUMENT } from '@angular/common';
import { computed, inject, Injectable, signal } from '@angular/core';

type ThemeMode = 'light' | 'dark';

const THEME_STORAGE_KEY = 'yappy-region-sync-theme';

@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly document = inject(DOCUMENT);

  readonly mode = signal<ThemeMode>(this.readStoredTheme());
  readonly isDark = computed(() => this.mode() === 'dark');

  constructor() {
    this.applyTheme(this.mode());
  }

  toggle(): void {
    this.setTheme(this.isDark() ? 'light' : 'dark');
  }

  private setTheme(mode: ThemeMode): void {
    this.mode.set(mode);
    this.applyTheme(mode);

    try {
      this.document.defaultView?.localStorage.setItem(THEME_STORAGE_KEY, mode);
    } catch {
      // El tema elegido se aplica aunque el navegador no permita guardar la preferencia.
    }
  }

  private readStoredTheme(): ThemeMode {
    try {
      return this.document.defaultView?.localStorage.getItem(THEME_STORAGE_KEY) === 'dark'
        ? 'dark'
        : 'light';
    } catch {
      return 'light';
    }
  }

  private applyTheme(mode: ThemeMode): void {
    this.document.documentElement.setAttribute('data-theme', mode);
  }
}
