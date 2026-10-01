import { Component, computed, inject, signal } from '@angular/core';
import { EnvironmentInfo, ExecuteSqlResponse, StatementResultInfo } from '../api-gen/models';
import { EnvironmentService } from '../core/services/environment.service';
import { DbService } from '../core/services/db.service';
import { toApiError } from '../core/services/api-error';
import { EnvPickerComponent } from '../shared/env-picker';
import { SchemaSelectComponent } from '../shared/schema-select';
import { StatusBadge } from '../shared/status-badge';

@Component({
  selector: 'app-compile-page',
  imports: [EnvPickerComponent, SchemaSelectComponent, StatusBadge],
  template: `
    <h1>Compilar / Ejecutar SQL</h1>
    <p class="muted">
      Elegí el ambiente, el tipo de objeto y pegá el código para ejecutarlo directo contra esa base.
      En Aurora/MySQL los stored procedures se (re)compilan con
      <code>CREATE [OR REPLACE] PROCEDURE</code>. Cada sentencia corre con autocommit activo.
    </p>

    <div class="panel">
      <div class="form-grid">
        <div>
          <label for="env">Ambiente</label>
          <app-env-picker
            id="env"
            [environments]="environments()"
            [selected]="envSelection()"
            [max]="1"
            (selectedChange)="onEnvSelection($event)"
          />
        </div>
        <div>
          <label>Tipo de objeto</label>
          <div class="radio-row">
            <label>
              <input
                type="radio"
                name="object-type"
                value="table"
                [checked]="objectType() === 'table'"
                (change)="objectType.set('table')"
              />
              Tabla
            </label>
            <label>
              <input
                type="radio"
                name="object-type"
                value="procedure"
                [checked]="objectType() === 'procedure'"
                (change)="objectType.set('procedure')"
              />
              Stored procedure
            </label>
          </div>
        </div>
        <div>
          <label for="schema">Schema (opcional)</label>
          <app-schema-select
            [env]="env()"
            [(value)]="schema"
            [optional]="true"
            emptyLabel="(sin schema)"
          />
        </div>
      </div>

      <label for="code">Código SQL / DDL</label>
      <textarea
        id="code"
        spellcheck="false"
        [value]="code()"
        (input)="code.set($any($event.target).value)"
        placeholder="Pegá acá el script, por ejemplo el que genera el DB Diff..."
      ></textarea>

      <label class="checkbox-row" style="margin-top:0.75rem;">
        <input
          type="checkbox"
          [checked]="confirmChecked()"
          (change)="confirmChecked.set($any($event.target).checked)"
        />
        <span>Sí, quiero ejecutar esto en <strong>{{ confirmEnv() }}</strong></span>
      </label>

      <div class="actions" style="justify-content:flex-end; margin-top:0.625rem;">
        <button type="button" [disabled]="busy()" (click)="run()">Compilar</button>
      </div>
    </div>

    @if (error()) {
      <div class="error-box">{{ error() }}</div>
    }

    @if (busy()) {
      <div class="panel"><span class="spinner"></span>Ejecutando en {{ env() || '…' }}...</div>
    }

    @if (result()) {
      @if (result()!.err_count === 0) {
        <div class="ok-box">
          <strong>Listo.</strong> {{ result()!.ok_count }} sentencia{{ result()!.ok_count === 1 ? '' : 's' }}
          ejecutada{{ result()!.ok_count === 1 ? '' : 's' }} correctamente en {{ result()!.env }}.
        </div>
      } @else {
        <div class="error-box">
          <strong>{{ result()!.err_count }} sentencia{{ result()!.err_count === 1 ? '' : 's' }} fallaron</strong>
          de {{ result()!.ok_count + result()!.err_count }} en {{ result()!.env }}.
        </div>
      }
      <div class="panel">
        @for (row of result()!.results; track row.index) {
          <div class="stmt-row">
            <span>#{{ row.index }}</span>
            <app-badge [status]="row.ok ? 'ok' : 'error'" [label]="row.ok ? 'OK' : 'Error'" />
            <div style="flex:1; min-width:0;">
              <pre class="stmt-preview">{{ row.sql }}</pre>
              <div class="muted stmt-meta">
                <span>{{ stmtMeta(row) }}</span>
                @if (row.error) {
                  <span style="color:var(--err);">{{ row.error }}</span>
                }
              </div>
            </div>
          </div>
        }
      </div>
    }
  `,
})
export class CompilePage {
  private readonly envService = inject(EnvironmentService);
  private readonly dbService = inject(DbService);

  readonly environments = signal<EnvironmentInfo[] | null>(null);
  readonly env = signal('');
  readonly objectType = signal<'table' | 'procedure'>('table');
  readonly schema = signal('');
  readonly code = signal('');
  readonly confirmChecked = signal(false);

  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly result = signal<ExecuteSqlResponse | null>(null);

  readonly confirmEnv = computed(() => this.env() || '…');

  /** El panel es multi-select; con `[max]="1"` refleja el único ambiente elegido. */
  readonly envSelection = computed(() => (this.env() ? [this.env()] : []));

  onEnvSelection(sel: string[]): void {
    this.env.set(sel[0] ?? '');
  }

  constructor() {
    this.envService.list().then(
      (envs) => this.environments.set(envs.environments),
      (err) => this.error.set('No se pudieron cargar los ambientes: ' + toApiError(err).message),
    );
  }

  stmtMeta(row: StatementResultInfo): string {
    const parts = [`${row.ms} ms`];
    if (row.ok && row.affected !== null && row.affected !== undefined) {
      parts.push(`${row.affected} filas`);
    }
    return parts.join(' · ');
  }

  run() {
    const env = this.env();
    const code = this.code();
    if (!env) {
      this.error.set('Seleccioná el ambiente.');
      return;
    }
    if (!code.trim()) {
      this.error.set('Pegá el código SQL que querés ejecutar.');
      return;
    }
    if (!this.confirmChecked()) {
      this.error.set('Confirmá que querés ejecutar esto en ' + env + '.');
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.dbService
      .executeSql({
        env,
        object_type: this.objectType(),
        schema_name: this.schema().trim(),
        code,
      })
      .then(
        (d) => {
          this.busy.set(false);
          this.result.set(d);
        },
        (err) => {
          this.busy.set(false);
          this.error.set(toApiError(err).message);
        },
      );
  }
}