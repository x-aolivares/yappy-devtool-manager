import { Component, computed, inject, signal } from '@angular/core';
import { DiffResponse, EnvironmentInfo } from '../api-gen/models';
import { EnvironmentService } from '../core/services/environment.service';
import { DbService } from '../core/services/db.service';
import { toApiError } from '../core/services/api-error';
import { CancelSlot, isCancellation } from '../core/cancel';
import { objectLabel } from '../core/format';
import { BusyModalComponent } from '../shared/busy-modal';
import { StatusBadge } from '../shared/status-badge';
import { CopyButton } from '../shared/copy-button';
import { RegionControlsComponent } from '../shared/region-controls';

@Component({
  selector: 'app-db-diff-page',
  imports: [RegionControlsComponent, StatusBadge, BusyModalComponent, CopyButton],
  template: `
    <h1>Diff de Base de Datos</h1>
    <p class="muted">
      Se comparan las dos regiones — <strong>origen → destino</strong> — y se genera el script
      para actualizar la región de destino con los cambios de la región de origen.
    </p>

    <div class="panel">
      <app-region-controls
        [environments]="environments()"
        [withService]="false"
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
          <div style="margin-top:0.75rem;">
            <label class="chk">
              <input
                type="checkbox"
                [checked]="includeDeletes()"
                (change)="includeDeletes.set($any($event.target).checked)"
              />
              Incluir eliminaciones
            </label>
            <p class="muted" style="margin-top:0.25rem; font-size:0.75rem;">
              Por lo general no se eliminan, pero podés activar el DROP para objetos que solo existen
              en la región destino.
            </p>
          </div>
        </div>
        <div class="actions" style="align-items:flex-end; justify-content:flex-end;">
          <button type="button" [disabled]="busy()" (click)="compare()">Comparar</button>
        </div>
      </div>
    </div>

    @if (error()) {
      <div class="error-box">{{ error() }}</div>
    }

    <app-busy-modal [open]="busy()" [message]="busyText()" (cancelled)="cancelBusy()" />

    @if (result()) {
      <div class="panel">
        <div style="display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap;">
          <app-badge [status]="result()!.status" />
          <span class="muted">
            {{ result()!.object_type }} {{ result()!.schema_name }}.{{ result()!.object_name }}
          </span>
          <span class="muted">{{ result()!.env_b }} → {{ result()!.env_a }}</span>
        </div>
        @for (n of result()!.notes ?? []; track n) {
          <div class="note">• {{ n }}</div>
        }
      </div>

      @if (result()!.status === 'missing_in_b') {
        <div class="panel">
          <div class="section-title"><strong>Región Destino ({{ result()!.env_a }})</strong></div>
          <pre>{{ pre(result()!.code_a, 'No existe en ' + result()!.env_b + ' (origen)') }}</pre>
        </div>
        @if (result()!.script) {
          <div class="panel">
            <div class="section-title">
              <strong>Script de eliminación (ejecutar en la región destino: {{ result()!.env_a }})</strong>
            </div>
            <pre class="script-block">{{ result()!.script }}</pre>
            <div class="actions" style="margin-top:0.625rem;">
              <app-copy-button [text]="result()!.script ?? ''" />
            </div>
          </div>
        }
      }

      @if (result()!.status === 'different' || result()!.status === 'missing_in_a') {
        <div class="columns">
          <div class="panel">
            <div class="section-title"><strong>Región de Origen — {{ result()!.env_b }}</strong></div>
            <pre>{{ pre(result()!.code_b, 'No existe en la región de origen') }}</pre>
          </div>
          <div class="panel">
            <div class="section-title"><strong>Región Destino — {{ result()!.env_a }}</strong></div>
            <pre>{{ pre(result()!.code_a, 'No existe en la región destino') }}</pre>
          </div>
        </div>
        <div class="panel">
          <div class="section-title">
            <strong>Script de actualización (ejecutar en la región destino: {{ result()!.env_a }})</strong>
          </div>
          @if (result()!.script) {
            <pre class="script-block">{{ result()!.script }}</pre>
            <div class="actions" style="margin-top:0.625rem;">
              <app-copy-button [text]="result()!.script ?? ''" />
            </div>
          } @else {
            <pre class="empty">No se detectaron cambios de estructura ejecutables.</pre>
          }
        </div>
      }
    }
  `,
})
export class DbDiffPage {
  private readonly envService = inject(EnvironmentService);
  private readonly dbService = inject(DbService);

  readonly environments = signal<EnvironmentInfo[] | null>(null);
  readonly envA = signal('');
  readonly envB = signal('');
  readonly schema = signal('');
  readonly objectName = signal('');
  readonly objectType = signal<'table' | 'procedure'>('table');
  readonly includeDeletes = signal(false);

  readonly busy = signal(false);
  readonly busyText = signal('');
  readonly error = signal<string | null>(null);
  readonly result = signal<DiffResponse | null>(null);

  /**
   * La comparación en vuelo, para que el botón del modal la corte.
   *
   * Sólo lee: lee el objeto en el origen y en el destino y arma el script. El
   * botón dice "Cancelar" y es literal —nada de lo que hace esta pantalla
   * escribe nada—.
   */
  private readonly op = new CancelSlot();

  constructor() {
    this.envService.list().then(
      (envs) => this.environments.set(envs.environments),
      (err) => this.error.set('No se pudieron cargar los ambientes: ' + toApiError(err).message),
    );
  }

  compare() {
    const schema = this.schema().trim();
    const objectName = this.objectName().trim();
    if (!this.envA() || !this.envB()) {
      this.error.set('Seleccioná las dos regiones.');
      return;
    }
    if (!schema || !objectName) {
      this.error.set('Completá el schema y el nombre del objeto.');
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.busyText.set(
      `Comparando ${objectLabel(this.objectType())} ${schema}.${objectName} de origen ${this.envB()} a destino ${this.envA()}...`,
    );

    const run = this.op.begin();
    this.dbService
      .diff(
        {
          env_a: this.envA(),
          env_b: this.envB(),
          schema_name: schema,
          object_type: this.objectType(),
          object_name: objectName,
          include_deletes: this.includeDeletes(),
        },
        run,
      )
      .then(
        (d) => {
          if (!this.op.finish(run)) return;
          this.busy.set(false);
          this.result.set(d);
        },
        (err) => {
          if (!this.op.finish(run)) return;
          this.busy.set(false);
          // Cortar la espera no es un fallo: el usuario lo pidió. Ver `core/cancel`.
          if (isCancellation(err)) return;
          this.error.set(toApiError(err).message);
        },
      );
  }

  /** Cortar la comparación en vuelo y dejar el diff anterior en pantalla. */
  cancelBusy(): void {
    if (!this.op.cancel()) return;
    this.busy.set(false);
  }

  pre(v: string | null | undefined, empty: string): string {
    return v ?? empty;
  }
}