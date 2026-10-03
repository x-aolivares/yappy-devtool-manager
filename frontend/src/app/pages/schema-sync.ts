import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { EnvironmentInfo, ExecuteSqlResponse, SchemaCompileResponse } from '../api-gen/models';
import { EnvironmentService } from '../core/services/environment.service';
import { DbService } from '../core/services/db.service';
import { toApiError } from '../core/services/api-error';
import { StatusBadge } from '../shared/status-badge';
import { CopyButton } from '../shared/copy-button';
import { RegionControlsComponent } from '../shared/region-controls';
import { SchemaSelectComponent } from '../shared/schema-select';
import { AutoGrowDirective } from '../shared/auto-grow';

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
 */
@Component({
  selector: 'app-schema-sync-page',
  imports: [
    RegionControlsComponent,
    SchemaSelectComponent,
    StatusBadge,
    CopyButton,
    AutoGrowDirective,
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

      <div class="checkbox-row" style="margin-top:0.75rem; gap:1.125rem; flex-wrap:wrap;">
        <label class="checkbox-row" style="margin:0;">
          <input
            type="checkbox"
            id="schema-sync-tables"
            [checked]="includeTables()"
            (change)="includeTables.set($any($event.target).checked)"
          />
          <span>Tablas</span>
        </label>
        <label class="checkbox-row" style="margin:0;">
          <input
            type="checkbox"
            id="schema-sync-procedures"
            [checked]="includeProcedures()"
            (change)="includeProcedures.set($any($event.target).checked)"
          />
          <span>Stored procedures</span>
        </label>
      </div>

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

    @if (result(); as r) {
      <div class="panel">
        <div class="section-title"><strong>Resultado</strong></div>
        <div style="display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap;">
          <app-badge [status]="r.status" />
          <span class="muted">
            {{ r.env_b }} → {{ r.env_a }} · {{ tables().length }} tabla(s) y
            {{ procedures().length }} procedimiento(s)
          </span>
        </div>

        @if (r.create_schema) {
          <div class="note">
            • El destino no tiene el esquema <code>{{ r.schema_name }}</code
            >: el script lo crea.
          </div>
        }
        @if (leftAlone().length) {
          <div class="note">
            • En {{ r.env_a }} quedan sin tocar {{ leftAlone().length }} tabla(s) que el origen no
            tiene:
            @for (t of leftAlone(); track t) {
              <code>{{ t }}</code>
            }
            No aparecen en el script.
          </div>
        }
        @for (n of r.notes ?? []; track n) {
          <div class="note">• {{ n }}</div>
        }
      </div>
    }

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
      <p class="muted hint-error" style="margin-top:0.5rem; font-size:0.75rem;">
        Ojo: el script borra y vuelve a crear {{ scopeLabel() }} en {{ envA() || 'el destino' }}.
        Las filas que están allá en esas tablas se pierden. Si querés conservar los datos del
        destino, editá el script y dejá solo los ALTER que faltan.
      </p>

      @if (!canSync() && envA()) {
        <p class="muted" style="margin-top:0.5rem; font-size:0.75rem;">
          Escribí o generá un script para habilitar el botón.
        </p>
      }
    </div>

    @if (error()) {
      <div class="error-box">{{ error() }}</div>
    }

    @if (busy()) {
      <div class="panel"><span class="spinner"></span>{{ busyText() }}</div>
    }

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

  readonly environments = signal<EnvironmentInfo[] | null>(null);
  readonly envB = signal(''); // origen
  readonly envA = signal(''); // destino: donde sincroniza
  readonly schema = signal('');
  readonly includeTables = signal(true);
  readonly includeProcedures = signal(true);

  /** The script the user runs, generated or written by hand. */
  readonly script = signal('');
  /** True only while the textarea holds something `/api/compile/schema` produced. */
  private readonly generated = signal(false);

  readonly busy = signal(false);
  readonly busyText = signal('');
  readonly error = signal<string | null>(null);
  readonly result = signal<SchemaCompileResponse | null>(null);
  readonly executed = signal<ExecuteSqlResponse | null>(null);

  /** The lists the response reports, defaulted so the template doesn't deal with nulls. */
  readonly tables = computed(() => this.result()?.tables ?? []);
  readonly procedures = computed(() => this.result()?.procedures ?? []);
  readonly leftAlone = computed(() => this.result()?.left_alone ?? []);

  /**
   * Un alcance vacío no sincroniza nada — el backend lo rechaza con un 400 — así
   * que se corta acá, en el botón, en lugar de dejar que viaje y vuelva el error.
   */
  readonly scopeSelected = computed(() => this.includeTables() || this.includeProcedures());

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
    if (!this.scopeSelected()) return 'Marcá al menos tablas o stored procedures.';
    return '';
  });

  /** Sincronizar does NOT need the schema chain: it runs whatever is in the editor. */
  readonly canSync = computed(() => !!this.envA() && this.script().trim().length > 0);

  /** Cómo se nombra el alcance en los avisos: "las tablas y los stored procedures". */
  readonly scopeLabel = computed(() => {
    const partes: string[] = [];
    if (this.includeTables()) partes.push('las tablas');
    if (this.includeProcedures()) partes.push('los stored procedures');
    return partes.length > 1 ? partes.join(' y ') : (partes[0] ?? 'los objetos del esquema');
  });

  constructor() {
    this.envService.list().then(
      (envs) => this.environments.set(envs.environments),
      (err) => this.error.set('No se pudieron cargar los ambientes: ' + toApiError(err).message),
    );

    // Un script generado describe un esquema, un alcance y un destino concretos. Si
    // el usuario cambia cualquiera de ellos —o el origen entero—, ejecutarlo sería
    // correrlo en el lugar equivocado, así que se descarta. Un script escrito a
    // mano es suyo: no se toca.
    effect(() => {
      this.envB();
      this.envA();
      this.schema();
      this.includeTables();
      this.includeProcedures();

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
      this.error.set('Marcá al menos tablas o stored procedures para sincronizar el schema.');
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.executed.set(null);
    this.busyText.set(`Generando el script de ${schema} de ${envB} a ${envA}...`);

    this.dbService
      .compileSchema({
        env_b: envB,
        env_a: envA,
        schema_name: schema,
        include_tables: this.includeTables(),
        include_procedures: this.includeProcedures(),
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
    const aviso =
      `\n\nEl script borra y vuelve a crear ${this.scopeLabel()} en ${env}. Las filas que ` +
      `están en ${env} en esos objetos se pierden: el script reemplaza la estructura y no ` +
      'copia los datos del origen. No es una fusión. Para conservar los datos del destino, ' +
      'editá el script y dejá solo los ALTER que faltan.';
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
        },
        (err) => {
          this.busy.set(false);
          this.error.set(toApiError(err).message);
        },
      );
  }
}
