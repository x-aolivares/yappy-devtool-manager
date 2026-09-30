/**
 * Estado de la lista de ambientes.
 *
 * Un solo fetch al inicio, cacheado: casi todas las features necesitan la lista
 * y `/environments` es de lo más barato de la API.
 */
import { Injectable, computed, inject, signal } from '@angular/core';
import { ApiService } from './api';
import { Environment } from './models';

@Injectable({ providedIn: 'root' })
export class EnvironmentsStore {
  private readonly api = inject(ApiService);

  private readonly _environments = signal<Environment[]>([]);
  private readonly _loading = signal(false);
  private readonly _error = signal<string | null>(null);
  private _loaded = false;

  readonly environments = this._environments.asReadonly();
  readonly loading = this._loading.asReadonly();
  readonly error = this._error.asReadonly();
  readonly names = computed(() => this._environments().map((e) => e.name));
  readonly isEmpty = computed(
    () => !this._loading() && this._loaded && this._environments().length === 0,
  );

  /** Idempotente: solo la primera llamada pega contra la API. */
  load(force = false): void {
    if (this._loaded && !force) return;

    this._loading.set(true);
    this._error.set(null);

    this.api.safe(this.api.listEnvironments()).subscribe({
      next: (list) => {
        this._environments.set(list);
        this._loading.set(false);
        this._loaded = true;
      },
      error: (err: Error) => {
        this._error.set(err.message);
        this._loading.set(false);
        this._loaded = true;
      },
    });
  }
}
