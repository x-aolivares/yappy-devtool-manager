import { Component, computed, inject, signal } from '@angular/core';
import { EnvironmentInfo, MigrationResponse, QueryResponse } from '../api-gen/models';
import { EnvironmentService } from '../core/services/environment.service';
import { DbService } from '../core/services/db.service';
import { toApiError } from '../core/services/api-error';
import { EnvControlsComponent } from '../shared/env-controls';
import { BusyModalComponent } from '../shared/busy-modal';
import { NoticeModalComponent } from '../shared/notice-modal';
import { PaginationBarComponent } from '../shared/pagination-bar';
import { TableSearchComponent, searchable } from '../shared/table-search';
import { paginate } from '../shared/paginate';
import { StatusBadge } from '../shared/status-badge';

/**
 * Ejecutar SQL: consultar información y migrar lo consultado.
 *
 * Son dos usos distintos sobre la misma consulta:
 *
 * 1. **Consultar** — correr un SELECT de lectura contra un ambiente y ver las
 *    filas. Solo se aceptan sentencias que leen.
 * 2. **Migrar info** — copiar a otro ambiente todas las tablas que la consulta
 *    toca, cada una con los filtros de la consulta. Si el
 *    SELECT une `a` y `b` con filtros, se migran las filas de `a` que cumplen
 *    esos filtros y las de `b` que también cumplen — no el resultado aplanado
 *    del join.
 *
 * El destino se elige solo al migrar: consultar no necesita dos ambientes.
 */
@Component({
  selector: 'app-sql-page',
  imports: [
    EnvControlsComponent,
    StatusBadge,
    BusyModalComponent,
    NoticeModalComponent,
    PaginationBarComponent,
    TableSearchComponent,
  ],
  template: `
    <h1>Ejecutar SQL</h1>
    <p class="muted">
      Corré una consulta de lectura contra un ambiente y mirá el resultado. También podés
      llamar un stored procedure con <code>CALL</code>: puede escribir, así que fijate qué hace
      antes de correrlo.
    </p>

    <div class="panel">
      <div class="field">
        <app-env-controls
          [environments]="environments()"
          [max]="1"
          envLabel="Ambiente"
          [(envs)]="envs"
          (envsChange)="onQueryEnvChange()"
        />
      </div>

      <div class="field">
        <label class="field-label" for="sql">Consulta SQL</label>
        <textarea
          id="sql"
          spellcheck="false"
          rows="8"
          [value]="sql()"
          (input)="sql.set($any($event.target).value)"
          placeholder="SELECT * FROM schema_abc.table_abc abc, schema_zxc.zxc zxc&#10;WHERE zxc.abc_id = abc.abc_id&#10;  AND zxc.zxc_status = 'COMPLETED'&#10;  AND abc.abc_type = 'M2P'&#10;&#10;-- o un stored procedure:&#10;CALL sp_abc('2026-03-01')"
        ></textarea>
      </div>

      <div class="actions" style="justify-content:flex-end;">
        <button type="button" [disabled]="busy()" (click)="runQuery()">Consultar</button>
      </div>
    </div>

    @if (error()) {
      <button type="button" class="error-marker" (click)="openNotice()">
        No se pudo consultar — ver el detalle
      </button>
    }

    <app-busy-modal [open]="busy()" [message]="busyText()" />

    @if (result()) {
      <div class="panel">
        <div style="display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap;">
          <span class="muted">{{ result()!.env }}</span>
          <span class="muted">{{ result()!.ms }} ms</span>
          <span class="muted">
            {{
              result()!.truncated
                ? 'primeras ' + rows().length + ' filas'
                : result()!.total + ' fila(s)'
            }}
          </span>
        </div>
        @if (result()!.truncated) {
          <div class="note">
            • El resultado es más largo de lo que se muestra. Agregá más filtros para ver todo.
          </div>
        }

        <div class="sql-grid-search">
          <app-table-search
            controlId="sql-grid-search"
            [query]="gridSearch.query()"
            label="Filtrar filas"
            placeholder="valor a buscar…"
            (changed)="gridSearch.query.set($event)"
          />
          <p class="muted sql-grid-search__hint">
            Busca en <strong>todas</strong> las columnas del SELECT, no en los nombres. Con
            {{ rows().length }} fila(s) es la única forma de encontrar una sin scrollear.
          </p>
        </div>
      </div>

      @if (rows().length) {
        <div class="panel">
          <div class="table-scroll">
            <table class="data-table">
              <thead>
                <tr>
                  @for (c of result()!.columns; track c) {
                    <th>{{ c }}</th>
                  }
                </tr>
              </thead>
              <tbody>
                @for (row of page.visible(); track $index) {
                  <tr>
                    @for (c of result()!.columns; track c) {
                      <td>{{ cell(row[c]) }}</td>
                    }
                  </tr>
                }
              </tbody>
            </table>
          </div>

          <app-pagination-bar [p]="page" />
        </div>
      } @else {
        <div class="panel"><pre class="empty">La consulta no devolvió filas.</pre></div>
      }
    }

    @if (showMigrate()) {
      <div class="panel">
        <div class="section-title"><strong>Migrar info</strong></div>
        <div class="field">
          <app-env-controls
            [environments]="destEnvironments()"
            [max]="1"
            envLabel="Ambiente destino"
            [(envs)]="destEnvs"
            (envsChange)="onDestEnvChange()"
          />
          @if (env() === destEnv()) {
            <p class="muted hint-error" style="margin-top:0.375rem; font-size:0.75rem;">
              El destino no puede ser el mismo ambiente de la consulta.
            </p>
          }
        </div>
        <div
          class="actions"
          style="justify-content:space-between; flex-wrap:wrap; gap:0.625rem;"
        >
          <label class="checkbox-row" style="margin:0;">
            <input
              type="checkbox"
              [checked]="confirmChecked()"
              (change)="confirmChecked.set($any($event.target).checked)"
            />
            <span>Sí, migrar a <strong>{{ destEnv() || '…' }}</strong></span>
          </label>
          <div class="actions" style="gap:0.625rem;">
            <button type="button" class="secondary" [disabled]="busy()" (click)="previewMigration()">
              Simular
            </button>
            <button
              type="button"
              [disabled]="busy() || !canMigrate()"
              (click)="runMigration()"
            >
              Migrar info
            </button>
          </div>
        </div>
      </div>
    }

    @if (migration()) {
      <div class="panel">
        <div style="display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap;">
          <span class="muted">{{ migration()!.env_b }} → {{ migration()!.env_a }}</span>
          @if (migration()!.dry_run) {
            <app-badge status="equal" label="Simulación" />
          }
        </div>
        @for (n of migration()!.notes ?? []; track n) {
          <div class="note">• {{ n }}</div>
        }
      </div>

      <div class="panel">
        <div class="section-title">
          <strong>
            @if (migration()!.dry_run) { Filas por tabla } @else { Resultado por tabla }
          </strong>
        </div>
        @for (t of migration()!.tables; track t.schema_name + '.' + t.table_name) {
          <div class="stmt-row">
            <app-badge [status]="t.ok ? 'ok' : 'error'" [label]="t.ok ? 'OK' : 'Error'" />
            <div style="flex:1; min-width:0;">
              <strong>{{ t.schema_name }}.{{ t.table_name }}</strong>
              @if (t.alias) {
                <span class="muted"> (alias {{ t.alias }})</span>
              }
              <div class="muted stmt-meta">
                @if (migration()!.dry_run) {
                  <span>{{ t.row_count }} fila(s)</span>
                } @else {
                  <span>{{ t.replaced }} de {{ t.row_count }} fila(s) migradas</span>
                }
                @if (t.skipped_columns?.length) {
                  <span>columnas no existentes en el destino, omitidas: {{ t.skipped_columns!.join(', ') }}</span>
                }
                @if (t.error) {
                  <span style="color:var(--err);">{{ t.error }}</span>
                }
              </div>
              <pre class="stmt-preview">{{ t.select_sql }}</pre>
            </div>
          </div>
        }
      </div>
    }

    <app-notice-modal
      [open]="noticeOpen()"
      [title]="noticeTitle()"
      (closed)="dismissNotice()"
    >
      <p class="notice-modal__text">{{ error() }}</p>
    </app-notice-modal>
  `,
  styles: `
    /* El filtro y su aclaración van juntos: separados, el hint parece otro bloque
       y el input queda huérfano. */
    .sql-grid-search {
      margin-top: 0.75rem;
    }
    .sql-grid-search__hint {
      margin-top: 0.375rem;
      font-size: var(--text-xs);
    }

    /* The inline marker: short on purpose, it only says there is a detail to
       read and reopens the modal. The long text lives in the modal. */
    .error-marker {
      display: block;
      width: 100%;
      text-align: left;
      padding: 0.625rem 0.875rem;
      border: 1px solid var(--err);
      border-radius: 6px;
      background: color-mix(in srgb, var(--err) 10%, transparent);
      color: var(--err);
      font: inherit;
      font-size: var(--text-sm);
      cursor: pointer;
    }
    .error-marker:hover {
      background: color-mix(in srgb, var(--err) 18%, transparent);
    }
  `,
})
export class SqlPage {
  private readonly envService = inject(EnvironmentService);
  private readonly dbService = inject(DbService);

  readonly environments = signal<EnvironmentInfo[] | null>(null);
  readonly envs = signal<string[]>([]);
  readonly sql = signal('');
  readonly confirmChecked = signal(false);
  readonly destEnvs = signal<string[]>([]);

  readonly busy = signal(false);
  readonly busyText = signal('');
  readonly error = signal<string | null>(null);
  readonly result = signal<QueryResponse | null>(null);
  readonly migration = signal<MigrationResponse | null>(null);

  readonly noticeOpen = signal(false);
  readonly noticeTitle = signal('No se pudo consultar');

  /**
   * The full error text goes in the modal, never in the line where it happened.
   *
   * The message that forced this is the tunnel failure: ~250 characters telling
   * you that the port is open but MySQL never answered, what to check (the
   * security group, DNS from the instance) and where the log is. That is a
   * paragraph, and a paragraph in `.error-box` pushes the whole page down — see
   * `docs/lenguaje-visual.md`, "Avisos que no caben en la línea donde ocurren".
   * What stays inline is a one-line marker that reopens the modal.
   *
   * Closing the modal is presentation only: `error()` keeps the text, so the
   * marker stays and reopening it is always possible.
   */
  private errorSeq = 0;
  private dismissedSeq = 0;

  openNotice(): void {
    this.noticeOpen.set(true);
  }

  /**
   * Show a failure and open its modal.
   *
   * The two counters make dismissing stick: `noticeOpen` is driven by
   * `errorSeq > dismissedSeq`, so a closed notice does not pop back open on the
   * next change detection, but a NEW failure does reopen it.
   */
  private fail(message: string, title = 'No se pudo consultar'): void {
    this.error.set(message);
    this.noticeTitle.set(title);
    this.errorSeq += 1;
    if (this.errorSeq > this.dismissedSeq) this.noticeOpen.set(true);
  }

  dismissNotice(): void {
    this.dismissedSeq = this.errorSeq;
    this.noticeOpen.set(false);
  }

  /** `rows` is optional in the contract; normalize it once for the template. */
  readonly rows = computed(() => this.result()?.rows ?? []);

  /**
   * Filtro de la grilla: busca en **todas** las columnas, no en los nombres.
   *
   * Es lo que lo hace útil. Con un SELECT de 500 filas y veinte columnas, scrollear
   * no encuentra una; buscar el valor sí. Por eso `toCells` proyecta la fila con
   * las columnas que el backend devolvió, en vez de sus claves: `JSON.stringify`
   * haría que buscar `id` encontrara cualquier fila, porque todas tienen esa
   * clave.
   */
  readonly gridSearch = searchable(
    () => this.rows(),
    (row) => (this.result()?.columns ?? []).map((c) => this.cell(row[c])),
  );

  /**
   * La grilla del resultado, filtrada y paginada.
   *
   * `rows()` sigue siendo el **total** —`showMigrate` y el aviso de "truncado"
   * dependen de cuántas hay, no de cuántas se ven—, así que filtrar o paginar no
   * puede hacer que "Migrar info" desaparezca.
   */
  readonly page = paginate(() => this.gridSearch.filtered());
  /** El ambiente elegido: el array es la fuente de verdad, el string se deriva. */
  readonly env = computed(() => (this.envs().length === 1 ? this.envs()[0] : ''));
  readonly destEnv = computed(() => (this.destEnvs().length === 1 ? this.destEnvs()[0] : ''));
  /** The destination picker excludes the query's own environment. */
  readonly destEnvironments = computed(() => {
    const all = this.environments();
    if (!all) return null;
    return all.filter((e) => e.env !== this.env());
  });

  /** Migration only makes sense on a query that actually returned something. */
  readonly showMigrate = computed(() => this.rows().length > 0);
  readonly canMigrate = computed(
    () => !!this.destEnv() && this.destEnv() !== this.env() && this.confirmChecked(),
  );

  constructor() {
    this.envService.list().then(
      (envs) => this.environments.set(envs.environments),
      (err) => this.fail('No se pudieron cargar los ambientes: ' + toApiError(err).message),
    );
  }

  /** Cambiar el ambiente de la consulta descarta la migración anterior. */
  onQueryEnvChange(): void {
    this.migration.set(null);
    this.confirmChecked.set(false);
    // El destino no puede coincidir con el ambiente de la consulta.
    if (this.env() && this.destEnvs().length === 1 && this.destEnvs()[0] === this.env()) {
      this.destEnvs.set([]);
    }
  }

  onDestEnvChange(): void {
    this.migration.set(null);
  }

  cell(value: unknown): string {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  runQuery() {
    const env = this.env();
    if (!env) {
      this.fail('Seleccioná el ambiente.');
      return;
    }
    if (!this.sql().trim()) {
      this.fail('Pegá la consulta que querés ejecutar.');
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.migration.set(null);
    this.confirmChecked.set(false);
    this.busyText.set(`Consultando ${env}...`);

    // Sin timeout del lado del cliente a propósito: el backend ya acota el
    // trabajo (probe del saludo de MySQL, `read_timeout` en la conexión y un
    // `max_execution_time` de sesión), así que la promesa siempre resuelve. Un
    // `setTimeout` acá solo sumaría un timer pendiente de 90s que deja la app
    // "inestable" en Angular zoneless, sin agregar ninguna protección real.
    this.dbService
      .query({ env, code: this.sql() })
      .then(
        (d) => {
          this.busy.set(false);
          this.result.set(d);
          // Cada consulta arranca limpia: la página y el filtro anteriores
          // describían otro resultado, y dejar el texto de búsqueda sobre una
          // consulta nueva esconde filas sin avisar.
          this.page.reset();
          this.gridSearch.query.set('');
        },
        (err) => {
          this.busy.set(false);
          this.fail(toApiError(err).message);
        },
      );
  }

  private migrate(dryRun: boolean): void {
    const text = this.sql().trim();
    if (!this.destEnv()) {
      this.fail('Elegí el ambiente destino.');
      return;
    }
    if (!dryRun && !this.confirmChecked()) {
      this.fail('Confirmá que querés migrar los datos a ' + this.destEnv() + '.');
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.busyText.set(
      dryRun
        ? `Simulando la migración de ${this.env()} a ${this.destEnv()}...`
        : `Migrando datos de ${this.env()} a ${this.destEnv()}...`,
    );

    this.dbService
      .migrate({
        env_b: this.env(),
        env_a: this.destEnv(),
        code: text,
        dry_run: dryRun,
        confirm: !dryRun,
      })
      .then(
        (d) => {
          this.busy.set(false);
          this.migration.set(d);
        },
        (err) => {
          this.busy.set(false);
          this.fail(toApiError(err).message, dryRun ? 'No se pudo simular' : 'No se pudo migrar');
        },
      );
  }

  previewMigration(): void {
    this.migrate(true);
  }

  runMigration(): void {
    this.migrate(false);
  }
}