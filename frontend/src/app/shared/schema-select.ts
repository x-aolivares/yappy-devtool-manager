import { Component, effect, inject, input, model, signal } from '@angular/core';
import { DbService } from '../core/services/db.service';
import { toApiError } from '../core/services/api-error';

/**
 * Schema picker backed by the live database.
 *
 * Opening a connection is expensive on real AWS (RDS token + SSM tunnel), so the
 * list is fetched once per environment selection and cached until `env` changes.
 * A response for a stale `env` is discarded to avoid races when switching fast.
 */
@Component({
  selector: 'app-schema-select',
  template: `
    <select
      [id]="controlId()"
      [disabled]="disabled()"
      [value]="value() || ''"
      (change)="value.set($any($event.target).value)">
      <option value="" [disabled]="!optional()" [selected]="!value()">{{ emptyLabel() }}</option>
      @for (s of schemas() ?? []; track s) {
        <option [value]="s" [selected]="value() === s">{{ s }}</option>
      }
    </select>
    @if (loading()) {
      <p class="muted" style="margin-top: 0.25rem; font-size: var(--text-xs);">
        <span class="spinner"></span> Cargando esquemas de {{ env() }}...
      </p>
    } @else if (error()) {
      <p class="muted hint-error" style="margin-top: 0.25rem; font-size: var(--text-xs);">
        No se pudieron leer los esquemas de {{ env() }}: {{ error() }}
      </p>
    } @else if (!env()) {
      <p class="muted" style="margin-top: 0.25rem; font-size: var(--text-xs);">
        Elegí un ambiente para ver sus esquemas.
      </p>
    }
  `,
})
export class SchemaSelect {
  private readonly dbService = inject(DbService);

  env = input('');
  value = model('');
  controlId = input('schema');
  /** When true the empty option stays selectable (schema is not mandatory). */
  optional = input(false);
  emptyLabel = input('Seleccione un esquema');

  readonly schemas = signal<string[] | null>(null);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  readonly disabled = signal(false);

  private requestSeq = 0;

  constructor() {
    effect(() => {
      const env = this.env();
      this.value.set('');
      if (!env) {
        this.requestSeq++;
        this.schemas.set(null);
        this.error.set(null);
        this.loading.set(false);
        this.disabled.set(true);
        return;
      }
      this.load(env);
    });
  }

  private load(env: string): void {
    const seq = ++this.requestSeq;
    this.loading.set(true);
    this.error.set(null);
    this.disabled.set(true);

    this.dbService.listSchemas(env).then(
      (res) => {
        if (seq !== this.requestSeq) return;
        this.schemas.set(res.schemas ?? []);
        this.loading.set(false);
        this.disabled.set(false);
      },
      (err) => {
        if (seq !== this.requestSeq) return;
        this.schemas.set(null);
        this.error.set(toApiError(err).message);
        this.loading.set(false);
        this.disabled.set(true);
      },
    );
  }
}