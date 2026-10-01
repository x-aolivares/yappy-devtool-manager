import { Component, computed, inject, signal } from '@angular/core';
import { CompileResponse, EnvironmentInfo } from '../api-gen/models';
import { EnvironmentService } from '../core/services/environment.service';
import { DbService } from '../core/services/db.service';
import { toApiError } from '../core/services/api-error';
import { objectLabel } from '../core/format';
import { StatusBadge } from '../shared/status-badge';
import { CopyButton } from '../shared/copy-button';
import { RegionControlsComponent } from '../shared/region-controls';

/**
 * Compilar: llevar un objeto de un ambiente a otro.
 *
 * El sentido siempre es origen -> destino. Lo usual es tomar el objeto de un
 * ambiente real y compilarlo en el local, pero nada acá lo asume: los dos
 * ambientes son eligeibles y el origen siempre es el que manda.
 *
 * - Stored procedure: se recompila entero desde la definición del origen.
 * - Tabla: no se sobrescribe. Se emiten solo las diferencias de columnas e
 *   índices como ALTER TABLE, para no perder los datos del destino.
 */
@Component({
  selector: 'app-compile-page',
  imports: [RegionControlsComponent, StatusBadge, CopyButton],
  template: `
    <h1>Compilar</h1>
    <p class="muted">
      Compila un objeto desde el ambiente de <strong>origen</strong> hacia el de
      <strong>destino</strong> — lo usual es de un ambiente real a
      <code>local</code>. Un stored procedure se recompila entero desde la definición del
      origen; una tabla no se sobrescribe: solo se aplican las diferencias de columnas e
      índices.
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
      <div class="form-grid">
        <div>
          <label for="schema">Schema</label>
          <input
            id="schema"
            type="text"
            [value]="schema()"
            (input)="schema.set($any($event.target).value)"
            placeholder="p. ej. yappy"
            spellcheck="false"
          />
        </div>
        <div>
          <label for="object-name">Nombre del objeto</label>
          <input
            id="object-name"
            type="text"
            [value]="objectName()"
            (input)="objectName.set($any($event.target).value)"
            placeholder="p. ej. users"
            spellcheck="false"
          />
        </div>
      </div>
      <div class="form-grid" style="grid-template-columns: 1fr 1fr;">
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
          <p class="muted" style="margin-top:0.5rem; font-size:0.75rem;">
            @if (objectType() === 'procedure') {
              Se recompila la definición completa en el destino.
            } @else {
              Se generan ALTER TABLE solo con lo que difiere; los datos del destino quedan.
            }
          </p>
        </div>
        <div class="actions" style="align-items:flex-end; justify-content:flex-end;">
          <button type="button" [disabled]="busy()" (click)="compile()">Compilar</button>
        </div>
      </div>
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

      @if (result()!.script) {
        <div class="panel">
          <div class="section-title">
            <strong>
              {{ result()!.object_type === 'procedure' ? 'Procedimiento recompilado' : 'DDL aplicado' }}
              en {{ result()!.env_a }} (destino)
            </strong>
          </div>
          <pre class="script-block">{{ result()!.script }}</pre>
          <div class="actions" style="margin-top:0.625rem;">
            <app-copy-button [text]="result()!.script ?? ''" />
          </div>
        </div>
      }

      @if (result()!.results?.length) {
        <div class="panel">
          <div class="section-title"><strong>Sentencias ejecutadas</strong></div>
          @for (row of result()!.results!; track row.index) {
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

  readonly busy = signal(false);
  readonly busyText = signal('');
  readonly error = signal<string | null>(null);
  readonly result = signal<CompileResponse | null>(null);

  readonly direction = computed(() => `${this.envB() || '…'} → ${this.envA() || '…'}`);

  constructor() {
    this.envService.list().then(
      (envs) => this.environments.set(envs.environments),
      (err) => this.error.set('No se pudieron cargar los ambientes: ' + toApiError(err).message),
    );
  }

  compile() {
    const schema = this.schema().trim();
    const objectName = this.objectName().trim();
    if (!this.envA() || !this.envB()) {
      this.error.set('Seleccioná el ambiente de origen y el de destino.');
      return;
    }
    if (this.envA() === this.envB()) {
      this.error.set('El origen y el destino tienen que ser distintos.');
      return;
    }
    if (!schema || !objectName) {
      this.error.set('Completá el schema y el nombre del objeto.');
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.busyText.set(
      `Compilando ${objectLabel(this.objectType())} ${schema}.${objectName} de ${this.envB()} a ${this.envA()}...`,
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
        },
        (err) => {
          this.busy.set(false);
          this.error.set(toApiError(err).message);
        },
      );
  }
}