import { Component, computed, effect, inject, signal } from '@angular/core';
import {
  DateColumnInfo,
  EnvironmentInfo,
  MigrationResponse,
  TableSelectionRequest,
} from '../api-gen/models';
import { EnvironmentService } from '../core/services/environment.service';
import { DbService } from '../core/services/db.service';
import { toApiError } from '../core/services/api-error';
import { StatusBadge } from '../shared/status-badge';
import { RegionControlsComponent } from '../shared/region-controls';
import { SchemaSelectComponent } from '../shared/schema-select';
import { BusyModalComponent } from '../shared/busy-modal';
import { NoticeModalComponent } from '../shared/notice-modal';
import { PaginationBarComponent } from '../shared/pagination-bar';
import { TableSearchComponent, searchable } from '../shared/table-search';
import { paginate } from '../shared/paginate';

/**
 * El estado de una tabla marcada para migrar.
 *
 * `columns === null` significa "todavía no se preguntó", que es distinto de `[]`
 * ("preguntado: no tiene columnas de fecha"). La diferencia decide qué se muestra
 * —los controles de filtro o el aviso de que se migra completa— y también si la
 * tabla se puede enviar: una consulta que falló no es lo mismo que una tabla sin
 * columnas de fecha, y mandarla entera sin querer es peor que frenar acá.
 *
 * `column === ''` es la opción "Todo": sin filtro. Cualquier otro valor es una
 * columna real de `columns`, nunca texto libre.
 */
interface TableState {
  readonly table: string;
  readonly included: boolean;
  readonly columns: DateColumnInfo[] | null;
  readonly loading: boolean;
  readonly error: string | null;
  /** '' = Todo (sin filtro). */
  readonly column: string;
}

function blankState(table: string): TableState {
  return {
    table,
    included: false,
    columns: null,
    loading: false,
    error: null,
    column: '',
  };
}

/** Cómo se acota la ventana de fechas. Es uno para toda la migración. */
type DateMode = 'day' | 'month' | 'range';

/** La ventana resuelta de un `DateMode` más sus campos, o `null` si está incompleta. */
interface DateWindow {
  readonly from: string;
  readonly to: string;
}

/**
 * The last day of a `YYYY-MM`, or `''` when the value isn't a month.
 *
 * `Date.UTC(year, mon, 0)` is day zero of the *next* month, which is the last
 * day of this one — the one thing `Date` arithmetic gets right about months
 * without a table of lengths, and correct for February in a leap year too.
 */
function lastDayOfMonth(month: string): string {
  const parts = /^(\d{4})-(\d{2})$/.exec(month);
  if (!parts) return '';
  const year = Number(parts[1]);
  const mon = Number(parts[2]);
  if (mon < 1 || mon > 12) return '';
  return `${month}-${String(new Date(Date.UTC(year, mon, 0)).getUTCDate()).padStart(2, '0')}`;
}

/**
 * Migrar datos: copiar un conjunto de tablas de un ambiente a otro, con un filtro
 * por fecha.
 *
 * Es el hermano de volumen de "Migrar info" (en *Ejecutar SQL*): allá el origen
 * de las filas es una consulta que escribe el usuario —con joins, alias y una
 * lógica de tres valores que hay que razonar—; acá es una lista de tablas del
 * origen, una por renglón, con a lo sumo un predicado cada una. El motor, el
 * `REPLACE INTO`, el orden de escritura, la reconciliación de columnas y el
 * `SELECT` que se devuelve son los mismos: sólo cambia de dónde sale el `SELECT`.
 *
 * **La ventana de fechas es una sola para toda la migración; la columna no.** La
 * columna de cada tabla es una pregunta distinta por tabla —`fecha` en una,
 * `created_at` en otra, y algunas no tienen ninguna— así que sigue en la fila. El
 * *período* es la misma pregunta para todas: "copiáseto de marzo". Ponerlo en cada
 * fila obligaba a repetir el mismo modo y las mismas fechas seis veces y dejaba
 * seis posibilidades de que una se quedara distinta, que es el error que esta
 * página existe para evitar.
 *
 * El filtro se arma con las columnas de fecha **reales** de la tabla
 * (`/api/db/date-columns`) y no con texto libre: el `<select>` sólo ofrece lo que
 * el origen tiene, y el backend valida el mismo nombre contra esa misma lista
 * antes de escribir una sola fila. Una tabla sin columnas DATE/DATETIME/TIMESTAMP
 * no se filtra — se migra completa, y la página lo dice en vez de esconder el
 * filtro detrás de un campo vacío.
 *
 * El modo del filtro se elige una vez para todas: un día, un mes o un rango. Los
 * dos primeros resuelven a una ventana cerrada y el tercero admite un solo
 * límite, porque "desde marzo en adelante" es una migración más y no hace falta
 * inventar un 2099 para expresarla. Un modo sin campo cargado **no** es "sin
 * filtro": es "todavía no está decidido", y frena el envío mientras haya alguna
 * tabla con columna elegida.
 *
 * Cada consulta es una conexión al origen, y abrirla no es gratis: por eso se
 * hacen **perezosamente**, sólo al marcar la tabla, y con un token de secuencia
 * para que una respuesta que llegó tarde no secriba sobre el estado de otra tabla.
 *
 * Migrar **escribe**. Por eso está partido en dos pasos: *Simular* lee y cuenta sin
 * escribir nada, y *Migrar* pone `confirm` detrás de un `window.confirm` que dice
 * lo que hace —escribe filas y reemplaza por primary key— en vez de dejarlo
 * implícito. Las advertencias que importan ("REPLACE no borra nada", "la tabla
 * tiene que existir en el destino", "no hay transacción") llegan en `notes` y se
 * muestran enteras: son la parte del contrato que evita que esto se use creyendo
 * que es otra cosa.
 */
@Component({
  selector: 'app-migrate-data-page',
  imports: [
    RegionControlsComponent,
    SchemaSelectComponent,
    StatusBadge,
    BusyModalComponent,
    NoticeModalComponent,
    PaginationBarComponent,
    TableSearchComponent,
  ],
  template: `
    <h1>Migrar datos</h1>
    <p class="muted">
      Copiá las filas de las tablas que marques a otro ambiente, con un filtro por fecha por tabla.
    </p>

    <div class="panel">
      <div class="columns">
        <div class="field">
          <app-region-controls
            [environments]="environments()"
            [withService]="false"
            envLabel="Ambientes"
            [(envB)]="envB"
            [(envA)]="envA"
          />
          @if (sameEnv()) {
            <p class="muted hint-error" style="margin-top:0.5rem; font-size:0.75rem;">
              El origen y el destino no pueden ser el mismo ambiente.
            </p>
          }
        </div>

        <div class="field">
          <label class="field-label" for="migrate-data-schema">Esquema del origen</label>
          <app-schema-select
            controlId="migrate-data-schema"
            [env]="envB()"
            [(value)]="schema"
            emptyLabel="Seleccione un esquema del origen"
          />
        </div>
      </div>

      @if (tablesLoading()) {
        <p class="muted" style="font-size:0.75rem;">
          <span class="spinner"></span> Cargando tablas de {{ schema() }} en {{ envB() }}...
        </p>
      } @else if (tablesError()) {
        <p class="muted hint-error" style="font-size:0.75rem;">
          No se pudieron leer las tablas de {{ schema() }}: {{ tablesError() }}
        </p>
      } @else if (schema() && !tableList().length) {
        <p class="muted" style="font-size:0.75rem;">
          {{ schema() }} no tiene tablas en {{ envB() }}: no hay nada para migrar.
        </p>
      }
    </div>

    @if (tableList().length) {
      <div class="panel">
        <div class="section-title section-title--plain">
          <strong>Filtro por fecha</strong>
          <span class="muted" style="font-size:0.75rem;">
            La misma ventana para todas las tablas marcadas
          </span>
        </div>

        <div class="radio-row">
          <label>
            <input
              type="radio"
              name="migrate-data-mode"
              [checked]="dateMode() === 'day'"
              (change)="setMode('day')"
            />
            Un día
          </label>
          <label>
            <input
              type="radio"
              name="migrate-data-mode"
              [checked]="dateMode() === 'month'"
              (change)="setMode('month')"
            />
            Un mes
          </label>
          <label>
            <input
              type="radio"
              name="migrate-data-mode"
              [checked]="dateMode() === 'range'"
              (change)="setMode('range')"
            />
            Rango
          </label>
        </div>

        <div class="migrate-data-window">
          @switch (dateMode()) {
            @case ('day') {
              <span class="field">
                <label class="field-label" for="migrate-data-day">Día</label>
                <input
                  type="date"
                  id="migrate-data-day"
                  [value]="day()"
                  (input)="day.set($any($event.target).value)"
                />
              </span>
            }
            @case ('month') {
              <span class="field">
                <label class="field-label" for="migrate-data-month">Mes</label>
                <input
                  type="month"
                  id="migrate-data-month"
                  [value]="month()"
                  (input)="month.set($any($event.target).value)"
                />
              </span>
            }
            @case ('range') {
              <div class="filter-table__range">
                <span>
                  <label class="field-label" for="migrate-data-from">Desde (inclusive)</label>
                  <input
                    type="date"
                    id="migrate-data-from"
                    [value]="dateFrom()"
                    (input)="dateFrom.set($any($event.target).value)"
                  />
                </span>
                <span>
                  <label class="field-label" for="migrate-data-to">Hasta (inclusive)</label>
                  <input
                    type="date"
                    id="migrate-data-to"
                    [value]="dateTo()"
                    (input)="dateTo.set($any($event.target).value)"
                  />
                </span>
              </div>
            }
          }

          @if (window(); as win) {
            <p class="muted migrate-data-window__note">
              @if (isSameDay(win)) {
                Se copia el día <strong>{{ win.from }}</strong> completo.
              } @else {
                Se copia del <strong>{{ win.from || '…' }}</strong> al
                <strong>{{ win.to || '…' }}</strong>, inclusive.
              }
            </p>
          }
        </div>
      </div>
    }

    @if (tableList().length) {
      <div class="panel">
        <app-table-search
          controlId="migrate-data-search"
          [query]="search.query()"
          label="Buscar tabla"
          placeholder="payment, order, log…"
          (changed)="search.query.set($event)"
        />

        <div class="section-title section-title--plain">
          <strong>Tablas a migrar</strong>
          <span class="actions">
            <span class="muted" style="font-size:0.75rem;">
              <!-- El total, SIEMPRE: es lo que avisa que hay tablas marcadas
                   fuera de lo que el filtro muestra. -->
              {{ includedTables().length }} de {{ tableList().length }} marcadas
            </span>
            <label class="checkbox-row" style="margin:0;">
              <input
                type="checkbox"
                id="migrate-data-all"
                [checked]="allIncluded()"
                (change)="toggleAll($any($event.target).checked)"
              />
              <span>Todas</span>
            </label>
          </span>
        </div>

        <div class="table-scroll">
          <table class="filter-table">
            <thead>
              <tr>
                <th>Tabla</th>
                <th>Columna de fecha</th>
              </tr>
            </thead>
            <tbody>
              @for (row of page.visible(); track row.table) {
                <tr [class.is-off]="!row.state?.included">
                  <td>
                    <label
                      class="checkbox-row"
                      style="margin:0;"
                      [for]="'migrate-table-' + row.table"
                    >
                      <input
                        type="checkbox"
                        [id]="'migrate-table-' + row.table"
                        [checked]="row.state?.included ?? false"
                        (change)="toggle(row.table, $any($event.target).checked)"
                      />
                      <span>{{ row.table }}</span>
                    </label>
                  </td>

                  @if (!row.state?.included) {
                    <td></td>
                  } @else if (row.state.loading) {
                    <td class="muted" style="font-size:0.75rem;">
                      <span class="spinner"></span> Buscando columnas…
                    </td>
                  } @else if (row.state.error) {
                    <td>
                      <button
                        type="button"
                        class="table-alert"
                        [attr.aria-label]="'Ver el detalle de ' + row.table"
                        (click)="noticeOpen.set(true)"
                      >
                        <app-badge status="error" label="No se pudieron leer las columnas" />
                      </button>
                    </td>
                  } @else if (!row.state.columns?.length) {
                    <td class="muted" style="font-size:0.75rem;">Sin columnas de fecha</td>
                  } @else {
                    <td>
                      <select
                        [id]="'migrate-date-column-' + row.table"
                        [value]="row.state.column"
                        (change)="setColumn(row.table, $any($event.target).value)"
                      >
                        <option value="" [selected]="!row.state.column">Todo (sin filtro)</option>
                        @for (c of row.state.columns!; track c.name) {
                          <option [value]="c.name" [selected]="row.state.column === c.name">
                            {{ c.name }} ({{ c.type }})
                          </option>
                        }
                      </select>
                    </td>
                  }
                </tr>
              }
            </tbody>
          </table>

          <app-pagination-bar [p]="page" />
        </div>
      </div>
    }

    <div class="panel">
      <div class="section-title section-title--plain"><strong>Simular o migrar</strong></div>
      <div class="actions" style="justify-content:space-between; flex-wrap:wrap; gap:0.625rem;">
        <span class="muted" style="font-size:0.75rem;">
          @if (submitHint(); as hint) {
            {{ hint }}
          }
        </span>
        <div class="actions" style="gap:0.625rem;">
          <button
            type="button"
            class="secondary"
            [disabled]="busy() || !canSubmit()"
            (click)="simulate()"
          >
            Simular
          </button>
          <button
            type="button"
            [disabled]="busy() || !canSubmit()"
            (click)="migrate()"
          >
            Migrar a {{ envA() || '…' }}
          </button>
        </div>
      </div>
    </div>

    @if (error()) {
      <div class="error-box">{{ error() }}</div>
    }

    <app-busy-modal [open]="busy()" [message]="busyText()" />

    @if (result(); as r) {
      <div class="panel">
        <div style="display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap;">
          <app-badge
            [status]="r.err_count ? 'error' : 'ok'"
            [label]="
              r.err_count
                ? r.ok_count + ' OK, ' + r.err_count + ' error(es)'
                : r.ok_count + ' tabla(s) OK'
            "
          />
          @if (r.dry_run) {
            <app-badge status="equal" label="Simulación" />
          }
          <span class="muted">{{ r.env_b }} → {{ r.env_a }}</span>
        </div>
      </div>

      <div class="panel">
        <div class="section-title">
          <strong>@if (r.dry_run) {Filas por tabla} @else {Resultado por tabla}</strong>
        </div>
        @for (t of r.tables ?? []; track t.schema_name + '.' + t.table_name) {
          <div class="stmt-row">
            <app-badge [status]="t.ok ? 'ok' : 'error'" [label]="t.ok ? 'OK' : 'Error'" />
            <div style="flex:1; min-width:0;">
              <strong>{{ t.schema_name }}.{{ t.table_name }}</strong>
              @if (t.alias) {
                <span class="muted"> (alias {{ t.alias }})</span>
              }
              <span class="muted">
                @if (t.target_schema) { → {{ t.target_schema }}.{{ t.target_table }} }
              </span>
              <div class="muted stmt-meta">
                <span>{{ t.row_count }} leída(s)</span>
                <span>{{ t.replaced }} reemplazada(s)</span>
                @if (t.skipped_columns?.length) {
                  <span>
                    columnas que no existen en el destino, omitidas:
                    {{ t.skipped_columns!.join(', ') }}
                  </span>
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

      @if (r.notes?.length) {
        <div class="panel">
          <div class="section-title"><strong>Advertencias</strong></div>
          @for (n of r.notes; track n) {
            <div class="note">• {{ n }}</div>
          }
        </div>
      }
    }

    <app-notice-modal
      [open]="noticeOpen()"
      title="No se pudieron leer las columnas de fecha"
      (closed)="dismissNotice()"
    >
      <p>
        La migración queda frenada. Sin saber si una tabla tiene columnas de fecha, mandarla sin
        filtro copiaría la tabla entera, así que primero hay que leer bien cuáles tiene.
      </p>
      <ul>
        @for (f of failedTables(); track f.table) {
          <li>
            No se pudieron leer las columnas de fecha de <code>{{ f.table }}</code>: {{ f.error }}
          </li>
        }
      </ul>
    </app-notice-modal>
  `,
  styles: `
    /* El filtro es de la página, no de una fila: los controles del modo van en su
       propia línea y el resumen de la ventana debajo, para que se lea como una
       decisión única y no como algo que se completa tabla por tabla. */
    .migrate-data-window {
      margin-top: 0.75rem;
    }
    .migrate-data-window__note {
      margin-top: 0.5rem;
      font-size: var(--text-xs);
    }
  `,
})
export class MigrateDataPage {
  private readonly envService = inject(EnvironmentService);
  private readonly dbService = inject(DbService);

  readonly environments = signal<EnvironmentInfo[] | null>(null);
  readonly envB = signal(''); // origen
  readonly envA = signal(''); // destino: donde migra
  readonly schema = signal('');

  /** The tables of the origin schema, loaded once per (origin, schema). */
  readonly tables = signal<string[] | null>(null);
  /** `tables` defaulted once, so the template doesn't deal with the null. */
  readonly tableList = computed(() => this.tables() ?? []);
  readonly tablesLoading = signal(false);
  readonly tablesError = signal<string | null>(null);

  /** One entry per table the user touched, marked or not. */
  readonly filters = signal<Record<string, TableState>>({});

  /**
   * The date filter is one decision for the whole migration, not one per table.
   *
   * It used to live in every row, which meant filling the same two fields and
   * picking the same mode six times to migrate six tables of the same window —
   * and six chances to pick six different windows by accident, which is the one
   * mistake this page exists to prevent. The column stays per table because it
   * genuinely differs (`fecha` in one table, `created_at` in another); the window
   * is the same question asked once.
   */
  readonly dateMode = signal<DateMode>('month');
  readonly day = signal('');
  readonly month = signal('');
  readonly dateFrom = signal('');
  readonly dateTo = signal('');

  readonly busy = signal(false);
  readonly busyText = signal('');
  readonly error = signal<string | null>(null);
  readonly result = signal<MigrationResponse | null>(null);

  readonly sameEnv = computed(() => !!this.envB() && this.envB() === this.envA());

  /**
   * The marked tables whose date columns could not be read. Drives the notice
   * modal: the detail of a failure is a couple of sentences, which is what broke
   * the table cell it used to live in. `canSubmit` still blocks on the same
   * `state.error`, so dismissing the modal does not unblock anything.
   */
  readonly failedTables = computed(() =>
    this.includedTables()
      .filter((t) => this.filters()[t]?.error)
      .map((t) => ({ table: t, error: this.filters()[t]?.error ?? '' })),
  );

  readonly noticeOpen = signal(false);

  /**
   * Bumped on every failed introspection. The modal opens for a NEW failure and
   * not for the one the user just closed, so dismissing it sticks until
   * something else breaks.
   */
  private errorSeq = 0;
  private dismissedSeq = 0;

  dismissNotice(): void {
    this.dismissedSeq = this.errorSeq;
    this.noticeOpen.set(false);
  }

  /** The marked tables, in the order the origin listed them. */
  readonly includedTables = computed(() => this.tableList().filter((t) => this.filters()[t]?.included));

  /**
   * One row per table of the origin schema, marked or not — the shape the filter
   * table renders. `state` is `undefined` until the user touches the table: that
   * is what tells the template "no filter yet" apart from "asked and there are
   * none", which are different answers and read differently.
   *
   * The date columns stay lazy. They are asked for when a table gets marked (see
   * `toggle`), so a table the user never selected never costs a round trip; its
   * row just shows the filter as not applying.
   */
  readonly allRows = computed(() =>
    this.tableList().map((table) => ({ table, state: this.filters()[table] })),
  );

  /**
   * Filtro por texto sobre el nombre de la tabla.
   *
   * Solo el nombre porque es lo único que la tabla tiene: la columna de fecha se
   * pide recién cuando la tabla se marca, así que no hay nada más que buscar
   * sin disparar una lectura por tabla.
   */
  readonly search = searchable(
    () => this.allRows(),
    (row) => [row.table],
  );

  /**
   * La tabla de tablas, filtrada y paginada.
   *
   * Igual que en Sincronizar schema, y por la misma razón: un esquema real tiene
   * cientos de tablas y la lista entera era lo que había que scrollear. **El
   * filtro va antes que la paginación**, por el mismo motivo.
   *
   * Y el filtro no cambia lo que se manda: `toggleAll`, `allIncluded` y `selection`
   * hablan de la lista **entera**. Filtrar es mirar, no elegir — si "Todas"
   * marcara sólo lo visible, filtrar por `payment` y tocar "Todas" desmarcaría las
   * otras cien tablas sin que el usuario las viera desaparecer.
   */
  readonly page = paginate(() => this.search.filtered());

  readonly allIncluded = computed(
    () => !!this.tableList().length && this.includedTables().length === this.tableList().length,
  );

  /**
   * The wire shape of the selection: one entry per marked table, and only the
   * window of the column that is actually chosen. "Todo" sends `null` column
   * *and* null dates — a column left over from before cannot filter a migration
   * that no longer wants a filter.
   */
  /**
   * The window the current mode and fields describe, or `null` when they don't
   * describe a complete one.
   *
   * `null` is not "no filter": it is "not decided yet", and it blocks the submit
   * for every table that picked a column. A half-filled range that silently
   * migrated the whole table is the accident `filterlessTables` exists for.
   */
  readonly window = computed<DateWindow | null>(() => {
    switch (this.dateMode()) {
      case 'day': {
        const day = this.day();
        return day ? { from: day, to: day } : null;
      }
      case 'month': {
        const month = this.month();
        const to = lastDayOfMonth(month);
        return to ? { from: `${month}-01`, to } : null;
      }
      case 'range': {
        const from = this.dateFrom();
        const to = this.dateTo();
        // One bound is a real request ("desde marzo en adelante"), so a half
        // range is a window — unlike the single-day and single-month modes,
        // where there is no partial answer.
        if (!from && !to) return null;
        if (from && to && from > to) return null; // al revés: nunca matchea nada
        return { from, to };
      }
    }
  });

  /** True when the mode picked is not the range, i.e. it needs exactly one field. */
  readonly needsOneField = computed(() => this.dateMode() !== 'range');

  readonly selection = computed<TableSelectionRequest[]>(() => {
    const win = this.window();
    return this.includedTables().map((table) => {
      const column = this.filters()[table]?.column ?? '';
      return {
        table,
        date_column: column || null,
        // The window belongs to the page, so every filtered table gets the same
        // one. A table with no column sends null bounds: it migrates whole.
        date_from: column && win ? win.from || null : null,
        date_to: column && win ? win.to || null : null,
      };
    });
  });

  /**
   * Marked tables that asked to be filtered by date and never said by how much.
   *
   * Choosing a column expresses "filter by this"; leaving both bounds empty does
   * not mean "nothing to migrate", it means **no filter at all** — `render_filter`
   * emits no predicate and the copy takes the whole table. That is the reading
   * nobody expects from a form with a mode set to "Rango" and two empty date
   * fields, and it is the one that dumps a year of production rows into dev.
   *
   * So this is not submittable. The honest way to say "the whole table" is the
   * `Todo (sin filtro)` option of the column select, which says exactly that.
   * The backend keeps its own note for this — it is the backstop for someone
   * calling the API directly, not the guard for this screen.
   *
   * One bound is fine and is not in here: "desde marzo en adelante" is a
   * migration like any other.
   */
  readonly filterlessTables = computed(() =>
    this.includedTables().filter((t) => !!this.filters()[t]?.column && !this.window()),
  );

  /**
   * The empty schema is not submittable: the backend answers a 400 for it, and a
   * migration of nothing is not a thing anybody meant to do.
   */
  readonly canSubmit = computed(() => {
    if (!this.envB() || !this.envA() || this.sameEnv() || !this.schema()) return false;
    if (!this.selection().length) return false;
    // Columna elegida sin ventana: el filtro no acotaría nada y la tabla entera
    // se copiaría. Ver `filterlessTables`.
    if (this.filterlessTables().length) return false;
    return this.includedTables().every((t) => {
      const state = this.filters()[t];
      return !!state && !state.loading && !state.error;
    });
  });

  /** Por qué está deshabilitado, en una línea. */
  readonly submitHint = computed(() => {
    if (!this.envB() || !this.envA()) return 'Elegí el ambiente de origen y el de destino.';
    if (this.sameEnv()) return 'El origen y el destino tienen que ser distintos.';
    if (!this.schema()) return 'Elegí el esquema del origen.';
    if (!this.includedTables().length) return 'Marcá al menos una tabla para migrar.';
    if (this.includedTables().some((t) => this.filters()[t]?.loading))
      return 'Esperando las columnas de fecha de las tablas marcadas.';
    if (this.includedTables().some((t) => this.filters()[t]?.error))
      return 'Hay una tabla marcada cuyas columnas de fecha no se pudieron leer.';

    // La ventana es una para toda la migración, así que el aviso es del filtro y
    // no de una tabla: una columna elegida sin ventana se copiaría entera.
    if (!this.window() && this.includedTables().some((t) => this.filters()[t]?.column)) {
      const reversed = this.dateMode() === 'range' && !!this.dateFrom() && !!this.dateTo();
      if (reversed) return 'La fecha inicial es posterior a la final: revisá el orden.';
      return (
        'Elegí la ventana de fechas: hay tablas con columna de fecha elegida y sin filtro, ' +
        'y se copiarían enteras. O completá la ventana, o elegí "Todo (sin filtro)" en esas tablas.'
      );
    }
    return '';
  });

  /**
   * Bumped when the (origin, schema) pair changes: every in-flight date-columns
   * request belongs to the schema the user just left.
   */
  private contextSeq = 0;
  /** Per table, so two loads of the same table can't race each other either. */
  private readonly columnSeq = new Map<string, number>();

  constructor() {
    this.envService.list().then(
      (envs) => this.environments.set(envs.environments),
      (err) => this.error.set('No se pudieron cargar los ambientes: ' + toApiError(err).message),
    );

    // The table list belongs to one (origin, schema). Changing either reloads it and
    // drops the selection: those filters described tables that may not even exist
    // in the new schema, and the columns they were built on are the old ones.
    effect(() => {
      const env = this.envB();
      const schema = this.schema();

      this.contextSeq++;
      this.columnSeq.clear();
      this.tables.set(null);
      this.tablesError.set(null);
      this.filters.set({});
      this.search.query.set('');
      this.loadTables(env, schema);
    });

    // Filtrar devuelve a la primera página: con la grilla en la página 5 y un
    // filtro que deja 2 tablas, la tabla queda vacía y la barra mintiendo.
    effect(() => {
      this.search.query();
      this.page.reset();
    });

    // A result describes one origin, one destination, one schema and one exact
    // selection. Touch any of them and the panel would be showing the migration of
    // something else, so it goes.
    effect(() => {
      this.envB();
      this.envA();
      this.schema();
      this.selection();
      this.result.set(null);
    });
  }

  private loadTables(env: string, schema: string): void {
    const seq = this.contextSeq;
    if (!env || !schema) {
      this.tables.set(null);
      this.tablesLoading.set(false);
      return;
    }

    this.tablesLoading.set(true);
    this.dbService.listObjects(env, schema, 'table').then(
      (res) => {
        if (seq !== this.contextSeq) return;
        this.tables.set(res.objects ?? []);
        this.tablesLoading.set(false);
      },
      (err) => {
        if (seq !== this.contextSeq) return;
        this.tables.set(null);
        this.tablesError.set(toApiError(err).message);
        this.tablesLoading.set(false);
      },
    );
  }

  /**
   * The date columns of one table, asked for the first time the table gets marked.
   *
   * One connection per table is not free, so this is deliberately lazy: a table
   * nobody marked never gets asked about. And it is guarded twice — by the context
   * token, which invalidates everything when the schema changes, and by a per-table
   * token, so a response for a table the user has since unmarked (or asked again)
   * writes nothing.
   */
  private loadDateColumns(table: string): void {
    const env = this.envB();
    const schema = this.schema();
    if (!env || !schema) return;

    const ctx = this.contextSeq;
    const seq = (this.columnSeq.get(table) ?? 0) + 1;
    this.columnSeq.set(table, seq);
    this.patch(table, { loading: true, error: null });

    this.dbService.dateColumns(env, schema, table).then(
      (res) => {
        if (!this.isCurrent(table, ctx, seq)) return;
        const columns = res.columns ?? [];
        const chosen = this.filters()[table]?.column ?? '';
        this.patch(table, {
          columns,
          // Only seed the default when nothing usable is chosen: re-reading the
          // columns must not throw away a column the user picked.
          column: columns.some((c) => c.name === chosen) ? chosen : (columns[0]?.name ?? ''),
          loading: false,
        });
      },
      (err) => {
        if (!this.isCurrent(table, ctx, seq)) return;
        // `columns: []` here would be a lie: it means "no date columns", and this
        // means "we couldn't find out". `canSubmit` blocks on the error.
        this.patch(table, { columns: null, column: '', loading: false, error: toApiError(err).message });
        this.errorSeq += 1;
        if (this.errorSeq > this.dismissedSeq) this.noticeOpen.set(true);
      },
    );
  }

  private isCurrent(table: string, ctx: number, seq: number): boolean {
    return ctx === this.contextSeq && this.columnSeq.get(table) === seq;
  }

  private patch(table: string, change: Partial<TableState>): void {
    const current = this.filters()[table] ?? blankState(table);
    this.filters.set({ ...this.filters(), [table]: { ...current, ...change } });
  }

  isIncluded(table: string): boolean {
    return !!this.filters()[table]?.included;
  }

  toggle(table: string, checked: boolean): void {
    this.patch(table, { included: checked });
    if (!checked) return;
    const state = this.filters()[table];
    // Lazy: the columns are asked for when the table joins the selection, and the
    // answer is kept so unmarking and remarking doesn't pay for it again.
    if (state && state.columns === null && !state.loading && !state.error) {
      this.loadDateColumns(table);
    }
  }

  toggleAll(checked: boolean): void {
    for (const t of this.tableList()) this.toggle(t, checked);
  }

  /** Only the columns the source really has; anything else is not offered. */
  setColumn(table: string, column: string): void {
    const state = this.filters()[table];
    const columns = state?.columns ?? [];
    const safe = column && columns.some((c) => c.name === column) ? column : '';
    // The window is not per table, so nothing else travels with the column.
    this.patch(table, { column: safe });
  }

  /**
   * Switching the mode keeps the window it described.
   *
   * The three modes write different fields, and clearing all of them would throw
   * away a month the user already picked just because they wanted to look at one
   * of its days. So the window resolved *before* the switch is carried into the
   * fields of the new mode, always the closest reading of what was already on
   * screen:
   *
   * - to **range**, the two bounds as they were;
   * - to **day**, the lower bound, which is the day the window opened on;
   * - to **month**, the month that lower bound falls in.
   *
   * Only those fields are touched. Switching to a month from a window that is
   * longer than a month widens it, and that is visible in the summary line right
   * below the controls rather than something to refuse over.
   */
  setMode(mode: DateMode): void {
    const previous = this.window();
    this.dateMode.set(mode);
    if (!previous?.from) return;
    if (mode === 'range') {
      this.dateFrom.set(previous.from);
      this.dateTo.set(previous.to);
    } else if (mode === 'day') {
      this.day.set(previous.from);
    } else {
      this.month.set(previous.from.slice(0, 7));
    }
  }

  isSameDay(win: DateWindow): boolean {
    return !!win.from && win.from === win.to;
  }

  simulate(): void {
    this.submit(true);
  }

  /**
   * Migrar writes. The confirmation says what it writes —rows in the destination,
   * replaced by primary key— because a dialog that says "¿continuar?" over a
   * `REPLACE INTO` is a dialog nobody can answer.
   */
  migrate(): void {
    const tables = this.includedTables().length;
    const destino = this.envA() || 'el destino';
    const aviso =
      `\n\nEsto escribe filas en ${destino}: cada fila que ya está en el destino y coincide ` +
      'en la primary key se reemplaza por la del origen. No borra las filas del destino que ' +
      'quedan fuera de la ventana, no crea las tablas que falten allá, y no corre en una ' +
      'transacción: si una tabla falla, las que venían antes ya quedaron copiadas.';
    if (
      !confirm(
        `¿Migrar ${tables} tabla(s) de ${this.schema() || '…'} de ${this.envB() || 'el origen'} a ` +
          `${destino}?${aviso}`,
      )
    )
      return;

    this.submit(false);
  }

  private submit(dryRun: boolean): void {
    const envB = this.envB();
    const envA = this.envA();
    const schema = this.schema();

    if (!envB || !envA) {
      this.error.set('Elegí el ambiente de origen y el de destino.');
      return;
    }
    if (this.sameEnv()) {
      this.error.set('El origen y el destino tienen que ser distintos.');
      return;
    }
    if (!schema) {
      this.error.set('Elegí el esquema del origen.');
      return;
    }
    if (!this.selection().length) {
      this.error.set('Marcá al menos una tabla para migrar.');
      return;
    }
    // El resto de lo que puede faltar es una tabla marcada cuyas columnas de fecha
    // todavía no se leyeron, o fallaron al leerse. Sin ese dato no se sabe si la
    // tabla se filtra o se copia entera, y mandarla a ciegas es justo el error que
    // esta página existe para no cometer.
    if (!this.canSubmit()) {
      this.error.set(this.submitHint());
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.busyText.set(
      dryRun
        ? `Simulando ${this.selection().length} tabla(s) de ${schema} de ${envB} a ${envA}...`
        : `Migrando ${this.selection().length} tabla(s) de ${schema} de ${envB} a ${envA}...`,
    );

    this.dbService
      .migrateTables({
        env_b: envB,
        env_a: envA,
        schema_name: schema,
        tables: this.selection(),
        dry_run: dryRun,
        confirm: !dryRun,
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
