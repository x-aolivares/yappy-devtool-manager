/**
 * Shell de la app: sidebar de features + barra superior con el selector de
 * ambiente.
 *
 * El ambiente es global a propósito: comparar o migrar entre dev y qa es el
 * flujo principal, y tener que re-seleccionarlo en cada pantalla rompe esa
 * lectura. Además, cambiarlo no reinicia la vista actual.
 */
import { Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { EnvContext } from './core/env-context';
import { EnvironmentsStore } from './core/environments.store';

interface NavItem {
  path: string;
  label: string;
  hint: string;
  icon: string;
}

type Theme = 'light' | 'dark';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  private readonly ctx = inject(EnvContext);
  private readonly store = inject(EnvironmentsStore);

  protected readonly envs = this.store.environments;
  protected readonly loadingEnvs = this.store.loading;
  protected readonly envError = this.store.error;
  protected readonly noEnvs = this.store.isEmpty;
  protected readonly selected = this.ctx.env;

  protected readonly nav: NavItem[] = [
    { path: '/', label: 'Inicio', hint: 'Estado y accesos rápidos', icon: '◈' },
    {
      path: '/parameters',
      label: 'Parámetros',
      hint: 'Buscar y resolver valores por ambiente',
      icon: '⌗',
    },
    {
      path: '/databases',
      label: 'Bases de datos',
      hint: 'Explorar schemas y migrar DDL a local',
      icon: '▤',
    },
    {
      path: '/query',
      label: 'Consultas SQL',
      hint: 'Ejecutar SELECT de solo lectura',
      icon: '⌘',
    },
  ];

  protected readonly theme = signal<Theme>(readTheme());

  constructor() {
    this.store.load();
    applyTheme(this.theme());

    // Si el ambiente guardado ya no existe (clone nuevo, otra rama), se cae al
    // primero disponible en vez de dejar la app apuntando a un nombre muerto.
    effect(() => {
      const names = this.store.names();
      const current = this.selected();
      if (names.length === 0) return;
      if (current === '' || !names.includes(current)) {
        this.ctx.select(names[0]);
      }
    });
  }

  protected selectEnv(event: Event): void {
    const value = (event.target as HTMLSelectElement).value;
    if (value) this.ctx.select(value);
  }

  protected toggleTheme(): void {
    const next: Theme = this.theme() === 'dark' ? 'light' : 'dark';
    this.theme.set(next);
    applyTheme(next);
    try {
      localStorage.setItem('yappy.theme', next);
    } catch {
      // Sin storage: el tema aplica igual, solo no se recuerda al recargar.
    }
  }

  protected readonly statusLine = computed(() => {
    const env = this.selected();
    return env
      ? `Trabajando contra «${env}»`
      : 'Elegí un ambiente para empezar';
  });
}

function readTheme(): Theme {
  try {
    const stored = localStorage.getItem('yappy.theme');
    if (stored === 'dark' || stored === 'light') return stored;
  } catch {
    // storage no disponible
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light';
}

function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme);
}
