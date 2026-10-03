import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { CompileResponse, EnvironmentInfo, ExecuteSqlResponse } from '../api-gen/models';
import { EnvironmentService } from '../core/services/environment.service';
import { DbService } from '../core/services/db.service';
import { toApiError } from '../core/services/api-error';
import { objectLabel } from '../core/format';
import { StatusBadge } from '../shared/status-badge';
import { CopyButton } from '../shared/copy-button';
import { EnvControlsComponent } from '../shared/env-controls';
import { RegionControlsComponent } from '../shared/region-controls';
import { SchemaSelectComponent } from '../shared/schema-select';
import { AutoGrowDirective } from '../shared/auto-grow';

/**
 * Compilar: ejecutar SQL contra un ambiente, tomando el SQL de donde sea.
 *
 * Lo único que cambia es el **origen**:
 *
 * - **Ambiente** — se leen dos ambientes, origen y destino. **Generar** arma el
 *   script desde el objeto del origen y lo deja en el editor.
 * - **Script** — un solo ambiente, el destino. El SQL es el que pega el usuario;
 *   no hay nada que generar ni con qué comparar.
 *
 * El destino siempre es `envA`, en los dos modos: en modo ambiente viene del par
 * de `app-region-controls`, en modo script del picker de un solo ambiente. Por eso
 * `canExecute` y el botón de Compilar no necesitan saber en qué modo están.
 *
 * En ambos casos la escritura es **Compilar**. **Generar** solo arma texto: no
 * escribe en ninguna base. El editor no distingue un script generado de uno
 * escrito a mano — se ejecuta tal cual está, previa confirmación.
 *
 * Compilar un objeto es un **reemplazo**, no una fusión: el origen es la verdad y
 * el script deja el destino igual al origen. Por eso todo lleva un
 * `DROP ... IF EXISTS` adelante y por eso el aviso de `replaceNotice` está a la
 * vista: el DROP de una tabla se lleva sus filas. Quien quiere conservar los datos
 * del destino edita el script a mano y deja solo los ALTER que faltan: no queda
 * una página que se los arme.
 */
@Component({
  selector: 'app-compile-page',
  imports: [
    EnvControlsComponent,
    RegionControlsComponent,
    SchemaSelectComponent,
    StatusBadge,
    CopyButton,
    AutoGrowDirective,
  ],
  template: `
    <h1>Compilar</h1>
    <p class="muted">
      Elegí el ambiente donde compilar y de dónde sale el SQL: de un
      <strong>ambiente de origen</strong> —se arma el script desde el objeto que hay allá— o de
      un <strong>script</strong> que pegás vos. En los dos casos lo que escribe en la base es
      <strong>Compilar</strong>, y compila siempre el mismo SQL: el destino queda igual al
      origen.
    </p>

    <div class="panel">
      <div class="columns">
        <div class="field">
          <div class="field-label">Origen del SQL</div>
          <div class="radio-row">
            <label>
              <input
                type="radio"
                name="compile-source"
                value="env"
                [checked]="source() === 'env'"
                (change)="setSource('env')"
              />
              Ambiente de origen
            </label>
            <label>
              <input
                type="radio"
                name="compile-source"
                value="script"
                [checked]="source() === 'script'"
                (change)="setSource('script')"
              />
              Script
            </label>
          </div>
        </div>

        <div class="field">
          @if (source() === 'env') {
            <app-region-controls
              [environments]="environments()"
              [withService]="false"
              envLabel="Ambientes"
              [(envB)]="envB"
              [(envA)]="envA"
            />
          } @else {
            <app-env-controls
              [environments]="environments()"
              [max]="1"
              envLabel="Ambiente"
              [envs]="destEnvs()"
              (envsChange)="onDestEnvChange($event)"
            />
            @if (envA()) {
              <div style="margin-top:0.875rem;">
                <label class="field-label" for="compile-script-schema">
                  Esquema (opcional)
                </label>
                <app-schema-select
                  controlId="compile-script-schema"
                  [env]="envA()"
                  [(value)]="schema"
                  [optional]="true"
                  emptyLabel="Sin USE — usá nombres calificados"
                />
              </div>
            }
          }
          @if (sameEnv()) {
            <p class="muted hint-error" style="margin-top:0.5rem; font-size:0.75rem;">
              El origen y el destino no pueden ser el mismo ambiente.
            </p>
          }
        </div>
      </div>

      @if (source() === 'env') {
        <div class="columns">
          <div class="field">
            <label class="field-label" for="compile-schema">
              Esquema del origen
            </label>
            <app-schema-select
              controlId="compile-schema"
              [env]="envB()"
              [(value)]="schema"
              emptyLabel="Seleccione un esquema del origen"
            />
          </div>

          <div class="field">
            <div class="field-label">Tipo de objeto</div>
            <div class="radio-row">
              <label [style.opacity]="schema() ? 1 : 0.5">
                <input
                  type="radio"
                  name="object-type"
                  value="table"
                  [disabled]="!schema()"
                  [checked]="objectType() === 'table'"
                  (change)="objectType.set('table')"
                />
                Tabla
              </label>
              <label [style.opacity]="schema() ? 1 : 0.5">
                <input
                  type="radio"
                  name="object-type"
                  value="procedure"
                  [disabled]="!schema()"
                  [checked]="objectType() === 'procedure'"
                  (change)="objectType.set('procedure')"
                />
                Stored procedure
              </label>
            </div>
          </div>
        </div>

        <div class="columns">
          <div class="field">
            <label class="field-label" for="compile-object">
              Nombre del objeto
            </label>
            <select
              id="compile-object"
              [disabled]="!schema() || objectsLoading()"
              [value]="objectName()"
              (change)="objectName.set($any($event.target).value)"
            >
              <option value="" [selected]="!objectName()" disabled>
                {{ objectPlaceholder() }}
              </option>
              @for (o of objects() ?? []; track o) {
                <option [value]="o" [selected]="objectName() === o">{{ o }}</option>
              }
            </select>
            @if (objectsLoading()) {
              <p class="muted" style="margin-top:0.25rem; font-size:0.75rem;">
                <span class="spinner"></span> Cargando {{ objectTypePlural() }} de {{ schema() }} en
                {{ envB() }}...
              </p>
            } @else if (objectsError()) {
              <p class="muted hint-error" style="margin-top:0.25rem; font-size:0.75rem;">
                No se pudieron leer los {{ objectTypePlural() }} de {{ schema() }}: {{ objectsError() }}
              </p>
            }
          </div>

          <div class="field" style="display:flex; flex-direction:column; justify-content:flex-end;">
            <div class="actions" style="justify-content:flex-end;">
              <button type="button" [disabled]="busy() || !canGenerate()" (click)="generate()">
                Generar
              </button>
            </div>
            @if (!canGenerate() && schema() && objectName()) {
              <p class="muted" style="margin-top:0.5rem; font-size:0.75rem;">
                Falta elegir el ambiente de destino para poder generar.
              </p>
            }
          </div>
        </div>
      }
    </div>

    <div class="panel">
      <div class="section-title section-title--plain">
        <strong>{{ source() === 'env' ? '6' : '3' }} · Script a ejecutar en {{ envA() || 'el destino' }}</strong>
        <span class="actions">
          <app-copy-button [text]="script()" />
          <button type="button" [disabled]="busy() || !canExecute()" (click)="run()">
            Compilar en {{ envA() || '…' }}
          </button>
        </span>
      </div>
      @if (source() === 'env') {
        <p class="muted" style="margin-bottom:0.5rem; font-size:0.75rem;">
          Arranca con un <code>DROP ... IF EXISTS</code>, así que el mismo script funciona exista
          o no exista el objeto allá.
        </p>
      }
      <textarea
        id="compile-script"
        appAutoGrow
        [autoGrowValue]="script()"
        spellcheck="false"
        rows="12"
        [value]="script()"
        (input)="script.set($any($event.target).value)"
        [placeholder]="
          source() === 'env'
            ? 'Generá el script con el botón de arriba, o pegá acá el SQL que quieras ejecutar.'
            : 'Pegá el SQL a compilar: un CREATE TABLE, un ALTER, un CREATE PROCEDURE...'
        "
      ></textarea>
      @if (replaceNotice(); as aviso) {
        <p class="muted hint-error" style="margin-top:0.5rem; font-size:0.75rem;">
          {{ aviso }}
        </p>
      }
      @if (!canExecute() && envA()) {
        <p class="muted" style="margin-top:0.5rem; font-size:0.75rem;">
          @if (source() === 'env') {
            Escribí o generá un script para poder ejecutarlo. No hace falta elegir un objeto:
            alcanza con el SQL.
          } @else {
            Pegá el SQL que querés compilar para habilitar el botón.
          }
        </p>
      }
    </div>

    @if (error()) {
      <div class="error-box">{{ error() }}</div>
    }

    @if (busy()) {
      <div class="panel"><span class="spinner"></span>{{ busyText() }}</div>
    }

    @if (result()) {
      <div class="panel">
        <div style="display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap;">
          <app-badge [status]="result()!.status" />
          <span class="muted">
            {{ objectLabel(result()!.object_type) }} {{ result()!.schema_name }}.{{
              result()!.object_name
            }}
          </span>
          <span class="muted">{{ result()!.env_b }} → {{ result()!.env_a }}</span>
        </div>
        @for (n of result()!.notes ?? []; track n) {
          <div class="note">• {{ n }}</div>
        }
      </div>
    }

    @if (executed()) {
      <div class="panel">
        <div style="display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap;">
          <app-badge
            [status]="executed()!.err_count ? 'error' : 'ok'"
            [label]="
              executed()!.err_count
                ? executed()!.err_count + ' error(es)'
                : executed()!.ok_count + ' sentencia(s) OK'
            "
          />
          <span class="muted">{{ executed()!.env }}</span>
        </div>
      </div>

      @if (executed()!.results?.length) {
        <div class="panel">
          <div class="section-title"><strong>Sentencias ejecutadas</strong></div>
          @for (row of executed()!.results!; track row.index) {
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
    }
  `,
})
export class CompilePage {
  private readonly envService = inject(EnvironmentService);
  private readonly dbService = inject(DbService);

  /** Exposed to the template for its labels. */
  protected readonly objectLabel = objectLabel;

  readonly environments = signal<EnvironmentInfo[] | null>(null);
  /** De dónde sale el SQL: otro ambiente, o un script del usuario. */
  readonly source = signal<'env' | 'script'>('env');
  readonly envB = signal(''); // origen; solo tiene sentido en modo ambiente
  readonly envA = signal(''); // destino: donde compila, en los dos modos
  readonly schema = signal('');
  readonly objectName = signal('');
  readonly objectType = signal<'table' | 'procedure'>('table');

  readonly objects = signal<string[] | null>(null);
  readonly objectsLoading = signal(false);
  readonly objectsError = signal<string | null>(null);

  /** The script the user executes, generated or written by hand. */
  readonly script = signal('');
  /** True only while the textarea holds something `/api/compile` produced. */
  private readonly generated = signal(false);

  readonly busy = signal(false);
  readonly busyText = signal('');
  readonly error = signal<string | null>(null);
  readonly result = signal<CompileResponse | null>(null);
  readonly executed = signal<ExecuteSqlResponse | null>(null);

  /** `app-env-controls` habla arrays y `envA` es un string: se adapta acá. */
  protected readonly destEnvs = computed(() => (this.envA() ? [this.envA()] : []));

  readonly objectTypePlural = computed(() =>
    this.objectType() === 'procedure' ? 'stored procedures' : 'tablas',
  );
  readonly objectPlaceholder = computed(() => {
    if (this.objects()?.length) return 'Seleccione un objeto';
    return `Sin ${this.objectTypePlural()} en ${this.schema() || 'el esquema'}`;
  });

  /**
   * Solo tiene sentido con dos ambientes: en modo script el destino es único y
   * cualquier coincidencia con el `envB` que quedó del otro modo es historia
   * vieja, no un error que haya que mostrar.
   */
  readonly sameEnv = computed(
    () => this.source() === 'env' && !!this.envB() && this.envB() === this.envA(),
  );

  /** Generar needs the whole chain: origin + destination, schema, type, object. */
  readonly canGenerate = computed(
    () =>
      !!this.envB() &&
      !!this.envA() &&
      !this.sameEnv() &&
      !!this.schema() &&
      !!this.objectType() &&
      !!this.objectName(),
  );

  /**
   * Compilar does NOT need the object chain: it runs whatever is in the editor.
   * It does need the destination, and that is `envA` in both modes — which is why
   * this check and the button don't care which origin is selected.
   */
  readonly canExecute = computed(() => !!this.envA() && this.script().trim().length > 0);

  /**
   * Aviso de reemplazo: compilar borra el objeto del destino y lo vuelve a crear.
   *
   * Para una tabla eso arrastra las filas, y es el motivo por el que este aviso
   * no es un detalle de la letra chica del `DROP`. También reemplaza el aviso
   * anterior ("no hay nada que aplicar"), que ya no puede darse: con
   * `DROP ... IF EXISTS` adelante hay script siempre, y era justamente ese aviso
   * el que dejaba al usuario mirando un `CREATE TABLE` pelado contra una tabla
   * que ya estaba, para que terminara en 1050.
   */
  readonly replaceNotice = computed<string | null>(() => {
    const r = this.result();
    if (!r) return null;
    if (r.status === 'missing_in_a') return null; // no hay nada que borrar
    if (r.object_type !== 'table') return null; // un procedure no tiene filas

    return (
      `Compilar ${r.schema_name}.${r.object_name} borra la tabla y todas sus filas en ` +
      `${r.env_a}, y la vuelve a crear con la definición de ${r.env_b}. ` +
      'Si querés conservar los datos del destino, editá el script y dejá solo ' +
      'los ALTER que faltan.'
    );
  });

  /** Discards responses for an object list the user already navigated away from. */
  private requestSeq = 0;

  constructor() {
    this.envService.list().then(
      (envs) => this.environments.set(envs.environments),
      (err) => this.error.set('No se pudieron cargar los ambientes: ' + toApiError(err).message),
    );

    // La lista de objetos depende del origen, del esquema y del tipo: se recarga
    // entera y se descarta la selección, que ya no existe en la nueva lista. En
    // modo script no hay ambiente de origen, así que no hay nada que listar.
    effect(() => {
      const env = this.source() === 'env' ? this.envB() : '';
      const schema = this.schema();
      const objectType = this.objectType();

      this.objectName.set('');
      this.loadObjects(env, schema, objectType);
    });

    // Un script generado describe un objeto y un destino concretos. Si el usuario
    // cambia cualquiera de los dos —o el origen entero—, ejecutarlo sería correrlo
    // en el lugar equivocado, así que se descarta. Un script escrito a mano es
    // suyo: no se toca.
    effect(() => {
      this.source();
      this.envA();
      this.envB();
      this.schema();
      this.objectType();
      this.objectName();

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

  private loadObjects(env: string, schema: string, objectType: 'table' | 'procedure'): void {
    if (!env || !schema) {
      this.requestSeq++;
      this.objects.set(null);
      this.objectsError.set(null);
      this.objectsLoading.set(false);
      return;
    }

    const seq = ++this.requestSeq;
    this.objectsLoading.set(true);
    this.objectsError.set(null);

    this.dbService.listObjects(env, schema, objectType).then(
      (res) => {
        if (seq !== this.requestSeq) return;
        this.objects.set(res.objects ?? []);
        this.objectsLoading.set(false);
      },
      (err) => {
        if (seq !== this.requestSeq) return;
        this.objects.set(null);
        this.objectsError.set(toApiError(err).message);
        this.objectsLoading.set(false);
      },
    );
  }

  /**
   * Cambia el origen del SQL. Es un corte y no un ajuste: lo que había en el
   * editor describía un objeto de un ambiente de origen, así que se descarta en
   * vez de quedar ahí listo para ejecutarse en otro lado. El esquema también se
   * va porque cambia de significado —de "el del objeto de origen" a "el USE
   * opcional"— y dejarlo puesto mandaría la ejecución a un schema callado.
   */
  setSource(source: 'env' | 'script'): void {
    if (source === this.source()) return;
    this.source.set(source);
    this.schema.set('');
    this.generated.set(false);
    this.script.set('');
    this.result.set(null);
    this.executed.set(null);
  }

  /** En modo script el destino es el único ambiente: va al mismo `envA`. */
  onDestEnvChange(envs: string[]): void {
    this.envA.set(envs.length === 1 ? envs[0] : '');
  }

  generate() {
    const schema = this.schema();
    const objectName = this.objectName();
    if (!this.envA() || !this.envB()) {
      this.error.set('Seleccioná el ambiente de origen y el de destino.');
      return;
    }
    if (this.sameEnv()) {
      this.error.set('El origen y el destino tienen que ser distintos.');
      return;
    }
    if (!schema || !objectName) {
      this.error.set('Completá el schema y elegí el objeto.');
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.executed.set(null);
    this.busyText.set(
      `Generando el script de ${objectLabel(this.objectType())} ${schema}.${objectName} de ${this.envB()} a ${this.envA()}...`,
    );

    this.dbService
      .compile({
        env_b: this.envB(),
        env_a: this.envA(),
        object_type: this.objectType(),
        schema_name: schema,
        object_name: objectName,
      })
      .then(
        (d) => {
          this.busy.set(false);
          this.result.set(d);
          // Cuando no hay nada que aplicar (ya es igual, o el DDL difiere pero la
          // estructura no) el editor se siembra con la definición que ya tiene el
          // destino: es el punto de partida para escribir el cambio a mano. Es
          // contenido generado, así que se descarta igual si cambia un selector.
          this.script.set(d.script || d.code_b || '');
          this.generated.set(true);
        },
        (err) => {
          this.busy.set(false);
          this.error.set(toApiError(err).message);
        },
      );
  }

  run() {
    const env = this.envA();
    const code = this.script();
    if (!env) {
      this.error.set('Elegí el ambiente destino.');
      return;
    }
    if (!code.trim()) {
      this.error.set('Pegá el SQL que querés ejecutar.');
      return;
    }

    const fromScript = this.source() === 'script';
    const schema = this.schema();
    const objectName = this.objectName();
    const target =
      !fromScript && schema && objectName ? `${schema}.${objectName} en ${env}` : `el script en ${env}`;

    // El script que genera Compilar arranca con un DROP, y hay que decirlo antes
    // de que corra, no después. Un `DROP TABLE` se lleva las filas del destino.
    const drops = /\bDROP\s+(TABLE|PROCEDURE|FUNCTION|TRIGGER)\b/i.test(code);
    const aviso = drops
      ? `\n\nOjo: el script borra lo que haya en ${env} — en una tabla, la tabla y todas sus ` +
        'filas. Compilar reemplaza; para conservar los datos del destino usá el Diff de base ' +
        'de datos.'
      : '';
    if (!confirm(`¿Ejecutar ${target}?\nCorre exactamente lo que está en el editor.${aviso}`))
      return;

    this.busy.set(true);
    this.error.set(null);
    this.busyText.set(`Ejecutando el script en ${env}...`);

    this.dbService
      .executeSql({
        env: env,
        // En modo script el texto no viene de un objeto con nombre propio: se
        // declara como lo que es para no etiquetar mal la respuesta.
        object_type: fromScript ? 'script' : this.objectType(),
        schema_name: schema,
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
