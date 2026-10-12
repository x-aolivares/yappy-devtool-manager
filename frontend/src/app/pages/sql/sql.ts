import { Component, computed, inject, signal } from '@angular/core';
import { EnvironmentInfo, MigrationResponse, QueryResponse } from '../../api-gen/models';
import { EnvironmentService } from '../../core/services/environment.service';
import { DbService } from '../../core/services/db.service';
import { toApiError } from '../../core/services/api-error';
import { CancelSlot, isCancellation } from '../../core/cancel';
import { EnvControls } from '../../components/env-controls';
import { BusyModal } from '../../components/busy-modal/busy-modal';
import { NoticeModal } from '../../components/notice-modal';
import { PaginationBar } from '../../components/pagination-bar';
import { TableSearch, searchable } from '../../components/table-search';
import { paginate } from '../../core/paginate';
import { StatusBadge } from '../../components/status-badge';

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
    EnvControls,
    StatusBadge,
    BusyModal,
    NoticeModal,
    PaginationBar,
    TableSearch,
  ],
  templateUrl: './sql.html',
  styleUrl: './sql.scss',
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
  /**
   * Si lo que está corriendo escribe.
   *
   * Consultar sólo lee; migrar escribe filas. El modal lo dice con otro rótulo
   * porque "Cancelar" sobre un `REPLACE INTO` en vuelo promete una cosa falsa.
   */
  readonly busyWrites = signal(false);
  readonly error = signal<string | null>(null);
  readonly result = signal<QueryResponse | null>(null);
  readonly migration = signal<MigrationResponse | null>(null);

  /** La operación que el modal de espera puede cortar. Ver `core/cancel`. */
  private readonly op = new CancelSlot();

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
    this.busyWrites.set(false);

    // Sin timeout del lado del cliente a propósito: el backend ya acota el
    // trabajo (probe del saludo de MySQL, `read_timeout` en la conexión y un
    // `max_execution_time` de sesión), así que la promesa siempre resuelve. Un
    // `setTimeout` acá solo sumaría un timer pendiente de 90s que deja la app
    // "inestable" en Angular zoneless, sin agregar ninguna protección real.
    const run = this.op.begin();
    this.dbService
      .query({ env, code: this.sql() }, run)
      .then(
        (d) => {
          if (!this.op.finish(run)) return;
          this.busy.set(false);
          this.result.set(d);
          // Cada consulta arranca limpia: la página y el filtro anteriores
          // describían otro resultado, y dejar el texto de búsqueda sobre una
          // consulta nueva esconde filas sin avisar.
          this.page.reset();
          this.gridSearch.query.set('');
        },
        (err) => {
          if (!this.op.finish(run)) return;
          this.busy.set(false);
          // Cortar la espera no es un fallo: el usuario lo pidió y no hay nada
          // que relatar. Ver `core/cancel`.
          if (isCancellation(err)) return;
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
    // Simular cuenta filas y no escribe; migrar hace `REPLACE INTO`. La
    // diferencia decide el rótulo del botón de cortar la espera.
    this.busyWrites.set(!dryRun);

    const run = this.op.begin();
    this.dbService
      .migrate(
        {
          env_b: this.env(),
          env_a: this.destEnv(),
          code: text,
          dry_run: dryRun,
          confirm: !dryRun,
        },
        run,
      )
      .then(
        (d) => {
          if (!this.op.finish(run)) return;
          this.busy.set(false);
          this.migration.set(d);
        },
        (err) => {
          if (!this.op.finish(run)) return;
          this.busy.set(false);
          if (isCancellation(err)) return;
          this.fail(toApiError(err).message, dryRun ? 'No se pudo simular' : 'No se pudo migrar');
        },
      );
  }

  /**
   * Dejar de esperar la consulta o la migración en vuelo.
   *
   * Corta la conexión y descarta la respuesta; no deshace lo que el backend
   * haya alcanzado a escribir, y por eso el modal lo dice con el rótulo de
   * "dejar de esperar" cuando la operación escribe.
   */
  cancelBusy(): void {
    if (!this.op.cancel()) return;
    this.busy.set(false);
  }

  previewMigration(): void {
    this.migrate(true);
  }

  runMigration(): void {
    this.migrate(false);
  }
}