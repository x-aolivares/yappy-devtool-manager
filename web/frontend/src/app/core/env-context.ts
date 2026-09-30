/**
 * Ambiente seleccionado, compartido por todas las features.
 *
 * Vive en un signal y se persiste en localStorage: cambiar de ambiente es una
 * decisión de sesión (comparás dev contra qa), no algo que deba re-preguntarse
 * en cada navegación.
 */
import { Injectable, computed, signal } from '@angular/core';

const STORAGE_KEY = 'yappy.selectedEnv';

@Injectable({ providedIn: 'root' })
export class EnvContext {
  private readonly _env = signal<string>(readStored());

  readonly env = this._env.asReadonly();
  readonly hasEnv = computed(() => this._env() !== '');

  select(env: string): void {
    this._env.set(env);
    try {
      localStorage.setItem(STORAGE_KEY, env);
    } catch {
      // Modo privado o storage deshabilitado: se pierde la preferencia entre
      // recargas, pero la app sigue funcionando.
    }
  }
}

function readStored(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? '';
  } catch {
    return '';
  }
}
