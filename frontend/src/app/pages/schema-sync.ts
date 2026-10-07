import { Component, PendingTasks, computed, effect, inject, signal, untracked } from '@angular/core';
import { EnvironmentInfo, ExecuteSqlResponse, SchemaCompileResponse } from '../api-gen/models';
import { EnvironmentService } from '../core/services/environment.service';
import { DbService } from '../core/services/db.service';
import { toApiError } from '../core/services/api-error';
import { BusyModalComponent } from '../shared/busy-modal';
import { StatusBadge } from '../shared/status-badge';
import { CopyButton } from '../shared/copy-button';
import { RegionControlsComponent } from '../shared/region-controls';
import { SchemaSelectComponent } from '../shared/schema-select';
import { AutoGrowDirective } from '../shared/auto-grow';
import { PaginationBarComponent } from '../shared/pagination-bar';
import { TableSearchComponent, searchable } from '../shared/table-search';
import { paginate } from '../shared/paginate';

/** One object of the origin schema, as the picker table renders it. */
interface SyncObject {
  readonly name: string;
  readonly kind: 'table' | 'procedure';
  readonly selected: boolean;
  /** Whether the destination already has it: the script replaces it and its rows. */
  readonly inDestination: boolean;
}

/**
 * What the destination has, per kind.
 *
 * Indexado por clase y no como un set plano porque son dos consultas distintas:
 * un procedure y una tabla nunca se confunden, y `SHOW PROCEDURE STATUS` /
 * `information_schema.ROUTINES` no devuelven las tablas. Mezclarlas en un solo
 * set haría que un procedure que existe en el destino se reportara como tabla.
 */
type DestinationIndex = Record<SyncObject['kind'], Set<string>>;

/**
 * Sincronizar schema: un solo script para todo un esquema, del ambiente de origen
 * al de destino.
 *
 * Es el hermano de volumen de *Compilar*: `/api/compile` mueve una tabla o un
 * stored procedure, `/api/compile/schema` mueve el esquema entero. El caso sólo
 * tiene sentido como un script único porque las tablas se referencian entre sí:
 * MySQL rechaza que se borre un padre al que una hija todavía apunta (3730), así
 * que el backend ordena los `DROP` hijos primero y los `CREATE` padres primero.
 *
 * Igual que Compilar, acá **generar no escribe**: el script vuelve en la
 * respuesta y queda en el editor, para que se lea antes de correrlo. Lo que
 * escribe es **Sincronizar**, previa confirmación.
 *
 * Y igual que Compilar, esto es un **reemplazo**, no una fusión: el origen es la
 * verdad y el destino queda con la estructura del origen. Por eso el script
 * empieza con `DROP TABLE IF EXISTS` y por eso la confirmación —y el aviso al
 * pie del editor— dicen que las filas del destino en esas tablas se pierden.
 * Quien quiere conservar los datos del destino edita el script a mano y deja
 * sólo los `ALTER` que faltan: no queda una página que se los arme.
 *
 * Lo que NO es un reemplazo es `left_alone`: las tablas que están sólo en el
 * destino no se tocan, no aparecen en el script y se listan aparte para que se
 * vean antes de correrlo. Sincronizar deja la copia del origen igual; no convierte
 * al destino en un clon.
 *
 * **Qué se sincroniza se ve antes de generar el script.** El alcance son las dos
 * casillas de "tablas" y "stored procedures", que sólo podían decir *todo o nada*
 * de cada clase. La pregunta real es "estas nueve, no esas cuatro", y sin una tabla
 * a la vista no hay forma de contestarla —ni de saber qué filas se van a perder.
 * Ahora cada objeto es una fila con su casilla y con la columna de si el destino ya
 * lo tiene, y la selección viaja explícita al backend.
 *
 * La columna "Existe en" se relee **después** de correr el script, porque un
 * objeto recién creado sigue diciendo "No, se crea" si nadie vuelve a preguntarle
 * al destino. Y lo pregunta por clase: una tabla y un procedure pueden llamarse
 * igual en MySQL, así que preguntar sólo por las tablas hacía que *todo* procedure
 * pareciera recién a crearse.
 */
@Component({
  selector: 'app-schema-sync-page',
  imports: [
    RegionControlsComponent,
    SchemaSelectComponent,
    StatusBadge,
    BusyModalComponent,
    CopyButton,
    AutoGrowDirective,
    PaginationBarComponent,
    TableSearchComponent,
  ],
  template: `
    <h1>Sincronizar schema</h1>
    <p class="muted">
      Toma todas las tablas y stored procedures de un esquema del ambiente de origen y arma un solo
      script para dejar el destino igual.
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
          <label class="field-label" for="schema-sync-schema">Esquema del origen</label>
          <app-schema-select
            controlId="schema-sync-schema"
            [env]="envB()"
            [(value)]="schema"
            emptyLabel="Seleccione un esquema del origen"
          />
        </div>
      </div>

      @if (objectsLoading()) {
        <p class="muted" style="margin-top:0.75rem; font-size:0.75rem;">
          <span class="spinner"></span> Cargando los objetos de {{ schema() }} en
          {{ envB() }}...
        </p>
      } @else if (objectsError()) {
        <p class="muted hint-error" style="margin-top:0.75rem; font-size:0.75rem;">
          No se pudieron leer los objetos de {{ schema() }}: {{ objectsError() }}
        </p>
      }

      @if (objectRows().length) {
        <div class="field">
          <app-table-search
            controlId="schema-sync-search"
            [query]="search.query()"
            label="Buscar objeto"
            placeholder="payment, sp_sync, tabla…"
            (changed)="search.query.set($event)"
          />

          <div class="section-title section-title--plain">
            <strong>Qué se sincroniza</strong>
            <span class="actions">
              <span class="muted" style="font-size:0.75rem;">
                <!-- El total, SIEMPRE. Es lo que avisa que hay filas marcadas
                     fuera de lo que el filtro muestra. -->
                {{ selectedCount() }} de {{ objectRows().length }} marcados
              </span>
              <label class="checkbox-row" style="margin:0;">
                <input
                  type="checkbox"
                  id="schema-sync-all"
                  [checked]="allSelected()"
                  [indeterminate]="someSelected()"
                  (change)="toggleAll($any($event.target).checked)"
                />
                <span>Todos</span>
              </label>
            </span>
          </div>

          <div class="table-scroll">
            <table class="filter-table filter-table--narrow">
              <thead>
                <tr>
                  <th>Objeto</th>
                  <th>Tipo</th>
                  <th>Existe en {{ envA() || 'el destino' }}</th>
                </tr>
              </thead>
              <tbody>
                @for (row of page.visible(); track row.kind + '.' + row.name) {
                  <tr [class.is-off]="!row.selected">
                    <td>
                      <label class="checkbox-row" style="margin:0;" [for]="'sync-obj-' + row.name">
                        <input
                          type="checkbox"
                          [id]="'sync-obj-' + row.name"
                          [checked]="row.selected"
                          (change)="toggle(row.name, $any($event.target).checked, row.kind)"
                        />
                        <span>{{ row.name }}</span>
                      </label>
                    </td>
                    <td>
                      <app-badge
                        [status]="row.kind === 'table' ? 'equal' : 'info'"
                        [label]="row.kind === 'table' ? 'tabla' : 'procedure'"
                      />
                    </td>
                    <td>
                      @if (row.inDestination) {
                        <app-badge status="equal" label="Sí" />
                      } @else {
                        <span class="muted" style="font-size:0.75rem;">No, se crea</span>
                      }
                    </td>
                  </tr>
                }
              </tbody>
            </table>

            <app-pagination-bar [p]="page" />

            @if (destinationError(); as err) {
              <p class="muted hint-error" style="margin-top:0.5rem; font-size:0.75rem;">
                No se pudo leer {{ envA() || 'el destino' }}: {{ err }}. La columna "Existe en"
                no se sabe y se muestra "No, se crea" aunque el objeto pueda estar.
              </p>
            }
          </div>
        </div>
      }

      <div
        class="field"
        style="display:flex; align-items:center; justify-content:space-between; gap:0.75rem; flex-wrap:wrap;"
      >
        @if (generateHint(); as hint) {
          <span class="muted" style="font-size:0.75rem;">{{ hint }}</span>
        } @else {
          <span></span>
        }
        <div class="actions">
          <button type="button" [disabled]="busy() || !canGenerate()" (click)="generate()">
            Generar
          </button>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="section-title section-title--plain">
        <strong>Script a ejecutar en {{ envA() || 'el destino' }}</strong>
        <span class="actions">
          <app-copy-button [text]="script()" />
          <button type="button" [disabled]="busy() || !canSync()" (click)="run()">
            Sincronizar en {{ envA() || '…' }}
          </button>
        </span>
      </div>
      <textarea
        id="schema-sync-script"
        appAutoGrow
        [autoGrowValue]="script()"
        spellcheck="false"
        rows="12"
        [value]="script()"
        (input)="script.set($any($event.target).value)"
        placeholder="Generá el script con el botón de arriba, o pegá acá el SQL que quieras ejecutar."
      ></textarea>
      @if (selected().tables.length) {
        <p class="muted hint-error" style="margin-top:0.5rem; font-size:0.75rem;">
          Ojo: el script borra y vuelve a crear {{ scopeLabel() }} en
          {{ envA() || 'el destino' }}. Las filas que están allá en esas tablas se pierden. Si
          querés conservar los datos del destino, editá el script y dejá solo los ALTER que faltan.
        </p>
      } @else {
        <p class="muted" style="margin-top:0.5rem; font-size:0.75rem;">
          Sólo se van a recrear stored procedures: no se toca ninguna tabla, así que no hay
          filas que perder.
        </p>
      }

      @if (!canSync() && envA()) {
        <p class="muted" style="margin-top:0.5rem; font-size:0.75rem;">
          Escribí o generá un script para habilitar el botón.
        </p>
      }
    </div>

    @if (error()) {
      <div class="error-box">{{ error() }}</div>
    }

    <app-busy-modal [open]="busy()" [message]="busyText()" />

    @if (executed(); as ex) {
      <div class="panel">
        <div class="section-title"><strong>Sentencias ejecutadas</strong></div>
        <div style="display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap;">
          <app-badge
            [status]="ex.err_count ? 'error' : 'ok'"
            [label]="ex.err_count ? ex.err_count + ' error(es)' : ex.ok_count + ' sentencia(s) OK'"
          />
          <span class="muted">{{ ex.env }}</span>
        </div>

        @for (row of ex.results ?? []; track row.index) {
          <div class="stmt-row">
            <span>#{{ row.index }}</span>
            <app-badge [status]="row.ok ? 'ok' : 'error'" [label]="row.ok ? 'OK' : 'Error'" />
            <div style="flex:1; min-width:0;">
              <pre class="stmt-preview">{{ row.sql }}</pre>
              <div class="muted stmt-meta">
                <span>{{ row.ms }} ms</span>
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
export class SchemaSyncPage {
  private readonly envService = inject(EnvironmentService);
  private readonly dbService = inject(DbService);
  private readonly pendingTasks = inject(PendingTasks);

  readonly environments = signal<EnvironmentInfo[] | null>(null);
  readonly envB = signal(''); // origen
  readonly envA = signal(''); // destino: donde sincroniza
  readonly schema = signal('');

  /**
   * The origin's tables and procedures, and which ones are marked.
   *
   * This replaces the two "include tables / include procedures" checkboxes. Those
   * could only say "todo o nada" of a kind, which is not the question anybody
   * asks: the question is "these nine, not those four". Seeing the objects is
   * also the only way to know *before* generating a script that drops and recreates
   * — and therefore empties — a table.
   *
   * The destination column answers "is this one already there?", because that is
   * what tells the user which rows the script is about to take away.
   */
  readonly objects = signal<SyncObject[] | null>(null);
  readonly objectsLoading = signal(false);
  readonly objectsError = signal<string | null>(null);
  /**
   * The destination read failed, but the origin's list did not.
   *
   * Separate from `objectsError` because the two mean opposite things: that one
   * says "there is no list, pick nothing", this one says "the list is fine but the
   * 'existe en' column is a guess". Confusing them would make a recoverable
   * problem look like the page is broken.
   */
  readonly destinationError = signal<string | null>(null);
  /** Marked objects, as `name` per kind — the shape `/api/compile/schema` takes. */
  readonly selected = signal<{ tables: string[]; procedures: string[] }>({
    tables: [],
    procedures: [],
  });

  /** The script the user runs, generated or written by hand. */
  readonly script = signal('');
  /** True only while the textarea holds something `/api/compile/schema` produced. */
  private readonly generated = signal(false);

  readonly busy = signal(false);
  readonly busyText = signal('');
  readonly error = signal<string | null>(null);
  readonly result = signal<SchemaCompileResponse | null>(null);
  readonly executed = signal<ExecuteSqlResponse | null>(null);

  /**
   * One row per object of the origin, tables first, alphabetical within each.
   *
   * Sale de `objects()`, no de `result()`: la lista de objetos que se puede marcar
   * es la del origen, que se lee al elegir el esquema. `result()` —lo que devolvió
   * `/api/compile/schema`— ya no se pinta en ningún lado, así que ni el estado ni
   * sus computeds tienen consumidor.
   */
  readonly objectRows = computed<SyncObject[]>(() => this.objects() ?? []);

  /**
   * Filtro por texto sobre el nombre y el tipo.
   *
   * Buscar `payment` tiene que encontrar `yappy_payment` **y** `sp_sync_payment`,
   * que es lo que hace que un solo control sirva para las dos clases de objeto.
   * El tipo entra como celda para que "buscar procedure" muestre sólo procedures.
   */
  readonly search = searchable(
    () => this.objectRows(),
    (row) => [row.name, row.kind === 'table' ? 'tabla' : 'procedure'],
  );

  /**
   * La tabla de objetos, filtrada y paginada.
   *
   * Un esquema real tiene cientos de objetos y la lista entera era lo que había
   * que scrollear. **El filtro va antes que la paginación**: al revés, buscar algo
   * que está en la fila 60 daría cero resultados en la página 1.
   *
   * Y el filtro no cambia lo que se manda: `selected`, `toggleAll` y `allSelected`
   * hablan de `objectRows()` — la lista **entera** —, no de la filtrada. Filtrar
   * es mirar, no elegir: si "Todos" marcara sólo lo que se ve, con el filtro puesto
   * desmarcaría 116 objetos que el usuario nunca tocó.
   */
  readonly page = paginate(() => this.search.filtered());

  /** Cuántos están marcados, en total. Es lo que se compara con la lista completa. */
  readonly selectedCount = computed(
    () => this.selected().tables.length + this.selected().procedures.length,
  );

  readonly allSelected = computed(
    () =>
      !!this.objectRows().length && this.selectedCount() === this.objectRows().length,
  );
  /** `Todos` a medio marcar, que es un estado distinto de "ninguno marcado". */
  readonly someSelected = computed(() => {
    const total = this.objectRows().length;
    const marked = this.selectedCount();
    return marked > 0 && marked < total;
  });

  /**
   * Un alcance vacío no sincroniza nada — el backend lo rechaza con un 400 — así
   * que se corta acá, en el botón, en lugar de dejar que viaje y vuelva el error.
   */
  readonly scopeSelected = computed(() => this.selectedCount() > 0);

  readonly sameEnv = computed(() => !!this.envB() && this.envB() === this.envA());

  /** Generar needs the whole chain: origin + destination + schema + a non-empty scope. */
  readonly canGenerate = computed(
    () =>
      !!this.envB() && !!this.envA() && !this.sameEnv() && !!this.schema() && this.scopeSelected(),
  );

  /** Por qué está deshabilitado Generar, en una línea. */
  readonly generateHint = computed(() => {
    if (!this.envB() || !this.envA()) return 'Elegí el ambiente de origen y el de destino.';
    if (this.sameEnv()) return 'El origen y el destino tienen que ser distintos.';
    if (!this.schema()) return 'Elegí el esquema del origen.';
    if (this.objectsLoading()) return 'Esperando los objetos del esquema de origen.';
    if (this.objectsError()) return 'No se pudieron leer los objetos del esquema de origen.';
    if (!this.objectRows().length) return 'El origen no tiene tablas ni procedures en ese esquema.';
    if (!this.scopeSelected()) return 'Marcá al menos una tabla o un stored procedure.';
    return '';
  });

  /** Sincronizar does NOT need the schema chain: it runs whatever is in the editor. */
  readonly canSync = computed(() => !!this.envA() && this.script().trim().length > 0);

  /**
   * Cómo se nombra el alcance en los avisos.
   *
   * Con lista de objetos puede ser "las 9 tablas", "3 tablas y 2 procedures" o una
   * sola cosa, así que cuenta en vez de decir "las tablas y los stored procedures":
   * el aviso tiene que decir cuántas filas se van a perder, no qué clases hay.
   */
  readonly scopeLabel = computed(() => {
    const t = this.selected().tables.length;
    const p = this.selected().procedures.length;
    const partes: string[] = [];
    if (t) partes.push(`${t} tabla${t === 1 ? '' : 's'}`);
    if (p) partes.push(`${p} procedure${p === 1 ? '' : 's'}`);
    return partes.length > 1 ? partes.join(' y ') : (partes[0] ?? 'los objetos marcados');
  });

  constructor() {
    this.envService.list().then(
      (envs) => this.environments.set(envs.environments),
      (err) => this.error.set('No se pudieron cargar los ambientes: ' + toApiError(err).message),
    );

    // Los objetos del esquema pertenecen a un (origen, esquema). Cambiar cualquiera
    // de los dos recarga la lista y limpia la selección: los nombres marcados eran
    // de otro esquema y probablemente ni siquiera existen en este.
    effect(() => {
      const env = this.envB();
      const schema = this.schema();
      this.search.query.set('');
      this.objectsSeq++;
      this.objects.set(null);
      this.objectsError.set(null);
      this.destinationError.set(null);
      this.selected.set({ tables: [], procedures: [] });
      // Registrada como tarea pendiente: la página está leyendo y su tabla de
      // objetos todavía no es la que va a quedar. Sin esto, un `whenStable()`
      // puede cerrarse en el medio de la cadena y dejar la tabla sin pintar.
      this.pendingTasks.run(() => Promise.resolve(this.loadObjects(env, schema)));
    });

    // Filtrar devuelve a la primera página. Sin esto, filtrar hasta dejar 2 objetos
    // con la grilla en la página 5 muestra la tabla vacía y la barra diciendo
    // "Página 5 de 1".
    effect(() => {
      this.search.query();
      this.page.reset();
    });

    // Un script generado describe un esquema, una selección y un destino concretos.
    // Si el usuario cambia cualquiera de ellos —o el origen entero—, ejecutarlo sería
    // correrlo en el lugar equivocado, así que se descarta. Un script escrito a
    // mano es suyo: no se toca.
    effect(() => {
      this.envB();
      this.envA();
      this.schema();
      this.selected();

      // `generated` se lee sin trackear a propósito: si se leyera trackeado, el
      // effect volvería a dispararse cuando `generate()` lo pone en true y
      // borraría el script que acaba de sembrar.
      if (!untracked(this.generated)) return;
      this.generated.set(false);
      this.script.set('');
      this.result.set(null);
      this.executed.set(null);
    });
  }

  /** Bumped when (origin, schema) changes: every pending object read is stale. */
  private objectsSeq = 0;

  /**
   * The origin's objects of one schema, plus which of them the destination has.
   *
   * Two requests, not one: `listTables` and `listProcedures` are the same endpoint
   * with `object_type`, so they are asked in parallel and joined here. The
   * destination's answer is what turns "se sincroniza" into "estas seis ya están
   * y el script se lleva sus filas".
   *
   * Guarded by `objectsSeq`: a schema the user already left must not write over the
   * one they are looking at now.
   */
  private loadObjects(env: string, schema: string): void {
    const seq = this.objectsSeq;
    if (!env || !schema) {
      this.objects.set(null);
      this.objectsLoading.set(false);
      return;
    }

    // Las lecturas del destino y las del origen en un `Promise.all` y no
    // encadenadas: encadenar la última dejaría su resolución fuera de lo que
    // espera un `whenStable()`, así que la tabla aparecería a destiempo.
    this.objectsLoading.set(true);
    const destino = env === this.envA() ? Promise.resolve(null) : this.readDestination(schema);
    Promise.all([
      this.dbService.listObjects(env, schema, 'table'),
      this.dbService.listObjects(env, schema, 'procedure'),
      destino,
    ]).then(
      ([tablas, procedures, delDestino]) => {
        if (seq !== this.objectsSeq) return;

        const origen: Array<{ name: string; kind: SyncObject['kind'] }> = [
          ...(tablas.objects ?? []).map((name) => ({ name, kind: 'table' as const })),
          ...(procedures.objects ?? []).map((name) => ({ name, kind: 'procedure' as const })),
        ];

        this.objects.set(
          origen.map((o) => ({
            ...o,
            selected: true,
            // `delDestino === null` es "no se pudo leer", no "no tiene nada": se
            // muestra "No, se crea" porque no se sabe, y el aviso lo dice al lado.
            inDestination: delDestino ? delDestino[o.kind].has(o.name) : false,
          })),
        );
        this.markAll(origen);
        this.objectsLoading.set(false);
      },
      (err) => {
        if (seq !== this.objectsSeq) return;
        this.objects.set(null);
        this.objectsError.set(toApiError(err).message);
        this.objectsLoading.set(false);
      },
    );
  }

  /**
   * What the destination has, per kind, or `null` when that read failed.
   *
   * **Las dos clases, siempre.** Preguntar sólo por tablas y dejar los procedures
   * como "no existe" los hacía mentir: un procedure ya compilado en el destino
   * salía "No, se crea" para siempre, y el `DROP PROCEDURE IF EXISTS` del
   * siguiente script lo reemplazaba sin que nadie lo supiera.
   *
   * A failure here must not sink the whole list: which objects exist in the origin
   * is the part the user needs to choose, and it arrives on its own. So the error
   * is reported next to the table and the "existe en" column falls back to not
   * knowing, rather than leaving the page empty.
   */
  private readDestination(schema: string): Promise<DestinationIndex | null> {
    const envA = this.envA();
    if (!envA) return Promise.resolve(null);
    return Promise.all([
      this.dbService.listObjects(envA, schema, 'table'),
      this.dbService.listObjects(envA, schema, 'procedure'),
    ]).then(
      ([tablas, procedures]) => ({
        table: new Set(tablas.objects ?? []),
        procedure: new Set(procedures.objects ?? []),
      }),
      (err) => {
        this.destinationError.set(toApiError(err).message);
        return null;
      },
    );
  }

  /**
   * Re-ask the destination what it has, leaving the selection alone.
   *
   * The column was a snapshot de cuando cargó la página, así que después de
   * correr el script seguía diciendo "No, se crea" para lo que el script acaba de
   * crear. Preguntar de nuevo es la única forma de que la columna diga la verdad:
   * suponer que el `CREATE` funcionó es exactamente lo que hace que uno crea
   * que compiló algo que no compiló.
   */
  private refreshDestination(): void {
    const schema = this.schema();
    if (!schema || !this.envA() || !this.objectRows().length) return;

    const seq = this.destinationSeq;
    this.destinationSeq++;
    this.readDestination(schema).then((delDestino) => {
      if (delDestino === null || seq !== this.destinationSeq) return;
      this.objects.set(
        this.objectRows().map((row) => ({
          ...row,
          inDestination: delDestino[row.kind].has(row.name),
        })),
      );
    });
  }

  private destinationSeq = 0;

  /** Everything starts marked: sincronizar el esquema means the schema. */
  private markAll(origen: Array<{ name: string; kind: SyncObject['kind'] }>): void {
    this.selected.set({
      tables: origen.filter((o) => o.kind === 'table').map((o) => o.name),
      procedures: origen.filter((o) => o.kind === 'procedure').map((o) => o.name),
    });
  }

  toggle(name: string, checked: boolean, kind?: SyncObject['kind']): void {
    this.patchObject(name, checked, kind);
  }

  /**
 * Marcar o desmarcar la lista **entera**, no lo que se ve con el filtro puesto.
 *
 * Es la contraparte de que el contador hable del total: si "Todos" marcara sólo lo
 * filtrado, filtrar por `payment` y tocar "Todos" desmarcaría los otros cien
 * objetos, que el usuario no vio desaparecer nunca.
 */
  toggleAll(checked: boolean): void {
    const rows = this.objectRows();
    this.objects.set(rows.map((r) => ({ ...r, selected: checked })));
    this.selected.set({
      tables: checked ? rows.filter((r) => r.kind === 'table').map((r) => r.name) : [],
      procedures: checked ? rows.filter((r) => r.kind === 'procedure').map((r) => r.name) : [],
    });
  }

  /**
   * Mark or unmark one object.
   *
   * Buscado por **clase y nombre**, no sólo por nombre: MySQL deja que una tabla
   * y un routine se llamen igual —viven en namespaces distintos— y buscar por
   * nombre solamente marcaría la fila que apareciera primero y dejaría la otra
   * como si no se pudiera tocar.
   */
  private patchObject(name: string, selected: boolean, kind?: SyncObject['kind']): void {
    const rows = this.objectRows();
    const row = rows.find((r) => r.name === name && (!kind || r.kind === kind));
    if (!row || row.selected === selected) return;
    const matches = (r: SyncObject) => r.name === row.name && r.kind === row.kind;
    this.objects.set(rows.map((r) => (matches(r) ? { ...r, selected } : r)));

    const bucket = row.kind === 'table' ? 'tables' : 'procedures';
    const names = new Set(this.selected()[bucket]);
    if (selected) names.add(row.name);
    else names.delete(row.name);
    this.selected.set({ ...this.selected(), [bucket]: [...names] });
  }

  generate(): void {
    const envB = this.envB();
    const envA = this.envA();
    const schema = this.schema();

    if (!envB || !envA) {
      this.error.set('Seleccioná el ambiente de origen y el de destino.');
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
    if (!this.scopeSelected()) {
      this.error.set('Marcá al menos una tabla o un stored procedure para sincronizar.');
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.executed.set(null);
    const selected = this.selected();
    const counted = `${selected.tables.length} tabla(s) y ${selected.procedures.length} procedure(s)`;
    this.busyText.set(`Generando el script de ${counted} de ${schema}, de ${envB} a ${envA}...`);

    this.dbService
      .compileSchema({
        env_b: envB,
        env_a: envA,
        schema_name: schema,
        include_tables: selected.tables.length > 0,
        include_procedures: selected.procedures.length > 0,
        // La lista explícita es lo que se compiló: sin ella el backend usaría los
        // flags y se llevaría el esquema entero. Las dos cosas viajan, y el
        // backend le da prioridad a la lista.
        tables: selected.tables,
        procedures: selected.procedures,
      })
      .then(
        (d) => {
          this.busy.set(false);
          this.result.set(d);
          // El backend devuelve el script siempre —para el caso "no hay nada que
          // reemplazar" también, que es un no-op— así que el editor se siembra con
          // lo que vino y el usuario lo ejecuta o lo edita.
          this.script.set(d.script || '');
          this.generated.set(true);
        },
        (err) => {
          this.busy.set(false);
          this.error.set(toApiError(err).message);
        },
      );
  }

  run(): void {
    const env = this.envA();
    const code = this.script();
    if (!env) {
      this.error.set('Elegí el ambiente destino.');
      return;
    }
    if (!code.trim()) {
      this.error.set('Generá o pegá el SQL que querés ejecutar.');
      return;
    }

    // Sincronizar reemplaza: hay que decirlo antes de que corra, no después. El
    // `DROP TABLE` del script se lleva las filas que están en el destino, y
    // empujar las del origen no es lo que hace esta página.
    const drops = /\bDROP\s+(TABLE|PROCEDURE|FUNCTION|TRIGGER)\b/i.test(code);
    const tablas = this.selected().tables.length;
    // El aviso cuenta las tablas, no los objetos: lo que se pierde son filas, y
    // una tabla es lo que tiene filas.
    const aviso =
      tablas > 0
        ? `\n\nEl script borra y vuelve a crear ${this.scopeLabel()} en ${env}. Las filas que ` +
          `están en ${env} en esas ${tablas} tabla(s) se pierden: el script reemplaza la ` +
          'estructura y no copia los datos del origen. No es una fusión. Para conservar los ' +
          'datos del destino, editá el script y dejá solo los ALTER que faltan.'
        : `\n\nEl script reemplaza ${this.scopeLabel()} en ${env}. No se borra ninguna tabla, ` +
          'así que no hay filas que perder.';
    if (
      !confirm(
        `¿Sincronizar ${this.schema() || 'el esquema'} de ${this.envB() || 'el origen'} a ${env}?\n` +
          `Corre exactamente lo que está en el editor.${drops ? aviso : ''}`,
      )
    )
      return;

    this.busy.set(true);
    this.error.set(null);
    this.busyText.set(`Sincronizando el schema en ${env}...`);

    this.dbService
      .executeSql({
        env: env,
        object_type: 'script',
        // Vacío a propósito, y no el esquema. `execute_sql` emite su propio
        // `USE <schema>` antes de la primera sentencia, así que mandar el nombre
        // lo manda contra una base que el destino puede no tener todavía
        // (`create_schema`), y el script falla con "Unknown database" en el
        // primer `CREATE TABLE`. El script generado ya trae su
        // `CREATE DATABASE IF NOT EXISTS` y su propio `USE`.
        schema_name: '',
        code: code,
      })
      .then(
        (d) => {
          this.busy.set(false);
          this.executed.set(d);
          // El script corrió: lo que estaba "No, se crea" puede existir ahora. Se
          // vuelve a preguntar al destino en vez de asumir que el `CREATE` funcionó
          // —la respuesta del `execute` es por sentencia, y una que falló deja el
          // objeto sin crear igual.
          this.refreshDestination();
        },
        (err) => {
          this.busy.set(false);
          this.error.set(toApiError(err).message);
        },
      );
  }
}
