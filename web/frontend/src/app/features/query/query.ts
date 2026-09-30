/**
 * Consola SQL de solo lectura.
 *
 * La restricción no es decorativa: el backend rechaza cualquier cosa que no sea
 * SELECT/SHOW/DESCRIBE/EXPLAIN/WITH, y el driver además corre sin
 * multi-statement. Esta UI lo dice de entrada para que nadie gaste un intento
 * en un DELETE y reciba un error en vez de una explicación.
 *
 * Hay historial local de las consultas escritas (no de los resultados): es lo
 * único que se guarda, en localStorage, y no se envía a ningún lado.
 */
import { Component, computed, inject, signal } from '@angular/core';
import { ApiService } from '../../core/api';
import { EnvContext } from '../../core/env-context';
import { QueryResult, Schema } from '../../core/models';
import { Alert, describe } from '../../shared/alert';

type Target = 'env' | 'local';
type ErrorInfo = { code: string; message: string; detail: string | null };

const HISTORY_KEY = 'yappy.queryHistory';
const HISTORY_LIMIT = 40;

@Component({
  selector: 'app-query',
  imports: [Alert],
  templateUrl: './query.html',
  styleUrl: './query.css',
})
export class Query {
  private readonly api = inject(ApiService);
  private readonly ctx = inject(EnvContext);

  protected readonly env = this.ctx.env;
  protected readonly hasEnv = this.ctx.hasEnv;

  protected readonly target = signal<Target>('env');
  protected readonly sql = signal('');
  protected readonly schema = signal('');
  protected readonly schemas = signal<Schema[]>([]);

  protected readonly running = signal(false);
  protected readonly error = signal<ErrorInfo | null>(null);
  protected readonly result = signal<QueryResult | null>(null);
  protected readonly history = signal<string[]>(readHistory());

  protected readonly canRun = computed(
    () => this.sql().trim() !== '' && !this.running() && this.hasEnv(),
  );

  protected readonly needsSchema = computed(() => this.target() === 'env');

  protected readonly cellIsNull = (value: unknown): boolean =>
    value === null || value === undefined;

  protected onSqlChange(event: Event): void {
    this.sql.set((event.target as HTMLTextAreaElement).value);
  }

  protected onTargetChange(event: Event): void {
    const value = (event.target as HTMLSelectElement).value as Target;
    this.target.set(value);
    this.error.set(null);
  }

  protected onSchemaChange(event: Event): void {
    this.schema.set((event.target as HTMLSelectElement).value);
  }

  protected loadSchemas(): void {
    const env = this.env();
    if (!env) return;
    this.api.safe(this.api.listSchemas(env)).subscribe({
      next: (list) => {
        this.schemas.set(list);
        if (this.schema() === '' && list.length > 0) {
          this.schema.set(list[0].name);
        }
      },
      error: (err: unknown) => this.error.set(describe(err)),
    });
  }

  protected run(): void {
    const env = this.env();
    const sql = this.sql().trim();
    if (!env || sql === '') return;

    if (this.needsSchema() && this.schema() === '') {
      this.error.set({
        code: 'SCHEMA_REQUIRED',
        message: 'Elegí un schema: la consulta va contra ese schema.',
        detail: null,
      });
      return;
    }

    this.running.set(true);
    this.error.set(null);

    this.api
      .safe(
        this.api.runQuery(
          env,
          sql,
          this.target() === 'env' ? this.schema() : null,
          this.target(),
        ),
      )
      .subscribe({
        next: (result) => {
          this.result.set(result);
          this.running.set(false);
          this.pushHistory(sql);
        },
        error: (err: unknown) => {
          this.result.set(null);
          this.error.set(describe(err));
          this.running.set(false);
        },
      });
  }

  private pushHistory(sql: string): void {
    const next = [sql, ...this.history().filter((s) => s !== sql)].slice(
      0,
      HISTORY_LIMIT,
    );
    this.history.set(next);
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
    } catch {
      // Sin storage: el historial es solo de esta sesión.
    }
  }

  protected use(entry: string): void {
    this.sql.set(entry);
  }

  protected clearHistory(): void {
    this.history.set([]);
    try {
      localStorage.removeItem(HISTORY_KEY);
    } catch {
      // ignore
    }
  }

  /**
   * Formatea una celda para mostrar. El nombre no es `cell` a propósito: en el
   * template la variable del `@for` se llama `cell` y taparía al método.
   */
  protected formatCell(value: unknown): string {
    if (value === null || value === undefined) return 'NULL';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

}

function readHistory(): string[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((s): s is string => typeof s === 'string')
      : [];
  } catch {
    return [];
  }
}
