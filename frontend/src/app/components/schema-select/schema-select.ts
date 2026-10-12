import { Component, effect, inject, input, model, signal } from '@angular/core';
import { DbService } from '../../core/services/db.service';
import { toApiError } from '../../core/services/api-error';

/**
 * Schema picker backed by the live database.
 *
 * Opening a connection is expensive on real AWS (RDS token + SSM tunnel), so the
 * list is fetched once per environment selection and cached until `env` changes.
 * A response for a stale `env` is discarded to avoid races when switching fast.
 */
@Component({
  selector: 'app-schema-select',
  templateUrl: './schema-select.html',
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