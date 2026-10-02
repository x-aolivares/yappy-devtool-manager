import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { CompileResponse, EnvironmentInfo, ExecuteSqlResponse } from '../api-gen/models';
import { EnvironmentService } from '../core/services/environment.service';
import { DbService } from '../core/services/db.service';
import { toApiError } from '../core/services/api-error';
import { objectLabel } from '../core/format';
import { StatusBadge } from '../shared/status-badge';
import { CopyButton } from '../shared/copy-button';
import { RegionControlsComponent } from '../shared/region-controls';
import { SchemaSelectComponent } from '../shared/schema-select';

/**
 * Compilar: llevar un objeto de un ambiente a otro, en dos pasos.
 *
 * El sentido siempre es origen -> destino. Lo usual es tomar el objeto de un
 * ambiente real y compilarlo en el local, pero nada acá lo asume: los dos
 * ambientes son eligeibles y el origen siempre es el que manda.
 *
 * - **Generar** solo arma el script: lee el origen, compara con el destino y
 *   devuelve el texto. No escribe en ninguna base. Cuando no hay nada que
 *   aplicar (ya son iguales, o el DDL difiere pero la estructura no), deja el
 *   editor sembrado con la definición que ya tiene el destino.
 * - **Compilar** ejecuta el texto del editor contra el destino. Como el texto es
 *   libre (generado, editado a mano o pegado desde donde sea), va con confirmación.
 *
 * - Stored procedure: se recompila entero desde la definición del origen.
 * - Tabla: no se sobrescribe. Se emiten solo las diferencias de columnas e
 *   índices como ALTER TABLE, para no perder los datos del destino.
 */
@Component({
  selector: 'app-compile-page',
  imports: [RegionControlsComponent, SchemaSelectComponent, StatusBadge, CopyButton],
  template: `
    <h1>Compilar</h1>
    <p class="muted">
      <strong>Generar</strong> arma el script sin tocar ninguna base: lee el objeto del ambiente de
      <strong>origen</strong>, lo compara con el de <strong>destino</strong> y te deja el SQL en el
      editor. <strong>Compilar</strong> es lo que lo ejecuta, ya sobre el destino — y podés editar el
      script o escribir uno propio desde cero.
    </p>

    <div class="panel">
      <app-region-controls
        [environments]="environments()"
        [withService]="false"
        envLabel="Ambientes"
        hint="Elegí dos: el primero es el de origen y el segundo el de destino."
        [(envB)]="envB"
        [(envA)]="envA"
      />

      <div class="section-title"><strong>1 · Esquema (del origen)</strong></div>
      <div class="form-grid">
        <div>
          <label for="compile-schema">Esquema</label>
          <app-schema-select
            controlId="compile-schema"
            [env]="envB()"
            [(value)]="schema"
            emptyLabel="Seleccione un esquema del origen"
          />
          @if (!envB()) {
            <p class="muted" style="margin-top:0.375rem; font-size:0.75rem;">
              Elegí el ambiente de origen para ver sus esquemas.
            </p>
          }
        </div>
      </div>

      <div class="section-title"><strong>2 · Tipo de objeto</strong></div>
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
      <p class="muted" style="margin-top:0.5rem; font-size:0.75rem;">
        @if (!schema()) {
          Elegí un esquema antes.
        } @else if (objectType() === 'procedure') {
          Se recompila la definición completa en el destino.
        } @else {
          Se generan ALTER TABLE solo con lo que difiere; los datos del destino quedan.
        }
      </p>

      <div class="section-title"><strong>3 · Objeto</strong></div>
      <div class="form-grid">
        <div>
          <label for="compile-object">Nombre del objeto</label>
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
          } @else if (!schema()) {
            <p class="muted" style="margin-top:0.25rem; font-size:0.75rem;">
              Elegí un esquema para ver sus objetos.
            </p>
          }
        </div>
        <div class="actions" style="align-items:flex-end; justify-content:flex-end;">
          <button type="button" [disabled]="busy() || !canGenerate()" (click)="generate()">
            Generar
          </button>
        </div>
      </div>
      @if (sameEnv()) {
        <p class="muted hint-error" style="margin-top:0.5rem; font-size:0.75rem;">
          El origen y el destino no pueden ser el mismo ambiente.
        </p>
      } @else if (!canGenerate() && schema() && objectName()) {
        <p class="muted" style="margin-top:0.5rem; font-size:0.75rem;">
          Falta elegir el ambiente de destino para poder generar.
        </p>
      }
    </div>

    <div class="panel">
      <div class="section-title">
        <strong>4 · Script a ejecutar en {{ envA() || 'el destino' }}</strong>
      </div>
      <p class="muted" style="margin-bottom:0.5rem; font-size:0.75rem;">
        Acá va el SQL que <strong>Compilar</strong> ejecuta en {{ envA() || 'el destino' }}, tal
        cual está. Si no hay nada que aplicar, queda sembrado con la definición que ya tiene el
        destino, para editarla a mano.
      </p>
      <textarea
        id="compile-script"
        spellcheck="false"
        rows="12"
        [value]="script()"
        (input)="script.set($any($event.target).value)"
        placeholder="Generá el script con el botón de arriba, o pegá acá el SQL que quieras ejecutar."
      ></textarea>
      @if (prefillNotice(); as aviso) {
        <p class="muted" style="margin-top:0.5rem; font-size:0.75rem;">
          {{ aviso }}
        </p>
      }
      <p class="muted" style="margin-top:0.5rem; font-size:0.75rem;">
        Podés editarlo, vaciarlo y escribir tu propio SQL. Se ejecuta tal cual está, statement por
        statement, en el ambiente destino.
      </p>

      <div class="actions" style="margin-top:0.75rem; justify-content:space-between; flex-wrap:wrap; gap:0.625rem;">
        <app-copy-button [text]="script()" />
        <button
          type="button"
          [disabled]="busy() || !canExecute()"
          (click)="run()"
        >
          Compilar en {{ envA() || '…' }}
        </button>
      </div>
      @if (!canExecute() && envA()) {
        <p class="muted" style="margin-top:0.5rem; font-size:0.75rem;">
          Escribí o generá un script para poder ejecutarlo. No hace falta elegir un objeto: alcanza
          con el SQL.
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
  readonly envB = signal(''); // origen
  readonly envA = signal(''); // destino
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

  readonly direction = computed(() => `${this.envB() || '…'} → ${this.envA() || '…'}`);
  readonly objectTypePlural = computed(() =>
    this.objectType() === 'procedure' ? 'stored procedures' : 'tablas',
  );
  readonly objectPlaceholder = computed(() => {
    if (this.objects()?.length) return 'Seleccione un objeto';
    return `Sin ${this.objectTypePlural()} en ${this.schema() || 'el esquema'}`;
  });

  readonly sameEnv = computed(() => !!this.envB() && this.envB() === this.envA());

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

  /** Compilar does NOT need the object chain: it runs whatever is in the editor. */
  readonly canExecute = computed(() => !!this.envA() && this.script().trim().length > 0);

  /**
   * Aviso para cuando no hay script que aplicar: el destino ya tiene el objeto con
   * la misma estructura y el editor quedó sembrado con su definición actual. No es
   * una barrera — el botón sigue habilitado — pero hay que decirlo: correr ese
   * texto tal cual falla porque el objeto ya existe en el destino.
   */
  readonly prefillNotice = computed<string | null>(() => {
    const r = this.result();
    if (!r || !r.code_b) return null;
    if (r.status !== 'equal' && !(r.status === 'different' && !r.script)) return null;
    const estado =
      r.status === 'equal'
        ? 'El destino ya es idéntico al origen, así que no hay nada que aplicar. '
        : 'El texto del DDL difiere pero la estructura ya es idéntica, así que no hay ' +
          'nada que aplicar. ';
    return (
      estado +
      'Abajo tenés la definición que ya tiene el destino: editala con el cambio que ' +
      'quieras llevar. Si la ejecutás sin editar, va a fallar porque el objeto ya existe allá.'
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
    // entera y se descarta la selección, que ya no existe en la nueva lista.
    effect(() => {
      const env = this.envB();
      const schema = this.schema();
      const objectType = this.objectType();

      this.objectName.set('');
      this.loadObjects(env, schema, objectType);
    });

    // Un script generado describe un objeto y un destino concretos. Si el usuario
    // cambia cualquiera de los dos, ejecutarlo sería correrlo en el lugar
    // equivocado, así que se descarta. Un script escrito a mano es suyo: no se toca.
    effect(() => {
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

    const schema = this.schema();
    const objectName = this.objectName();
    const target =
      schema && objectName ? `${schema}.${objectName} en ${env}` : `el script en ${env}`;
    if (!confirm(`¿Ejecutar ${target}?\nCorre exactamente lo que está en el editor.`)) return;

    this.busy.set(true);
    this.error.set(null);
    this.busyText.set(`Ejecutando el script en ${env}...`);

    this.dbService
      .executeSql({
        env: env,
        object_type: this.objectType(),
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
