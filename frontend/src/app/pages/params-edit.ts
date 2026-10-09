import { Component, computed, inject, signal } from '@angular/core';
import { EnvironmentInfo } from '../api-gen/models';
import { EnvironmentService } from '../core/services/environment.service';
import { ParamsService } from '../core/services/params.service';
import { toApiError } from '../core/services/api-error';
import { CancelSlot, isCancellation } from '../core/cancel';
import { awsEnvironments } from '../core/format';
import { BusyModal } from '../shared/busy-modal';
import { EnvControls } from '../shared/env-controls';

@Component({
  selector: 'app-params-edit-page',
  imports: [BusyModal, EnvControls],
  template: `
    <h1>Editar / actualizar un parámetro</h1>
    <p class="muted">
      Elegís un ambiente y un nombre: el valor se lee (con decodificación si es
      <code>SecureString</code>), lo editás y lo guardás con <strong>tu confirmación</strong>.
      Si no existe el parámetro, podés crearlo desde acá (overwrite). Si el parámetro
      guarda un secreto, marcá <strong>"Este parámetro almacena un secreto"</strong> y
      completá el nombre y valor del secreto; el valor del parámetro se guarda como el nombre del secreto.
    </p>

    <div class="panel">
      <app-env-controls [environments]="awsEnvironments()" [max]="1" [(envs)]="envs" />

      <label for="name" style="margin-top:1rem;">Nombre del parámetro</label>
      <input
        id="name"
        type="text"
        [value]="name()"
        (input)="name.set($any($event.target).value)"
        placeholder="/yappy/dev/config"
        spellcheck="false"
        style="max-width: 26.25rem;"
        (keydown.enter)="read()"
      />

      <div class="actions" style="justify-content:flex-end; margin-top:0.625rem;">
        <button type="button" class="secondary" [disabled]="busy()" (click)="read()">Leer</button>
      </div>
    </div>

    @if (error()) {
      <div class="error-box">{{ error() }}</div>
    }

    @if (busy() && !loaded()) {
      <div class="panel"><span class="spinner"></span>Leyendo…</div>
    }

    @if (loaded()) {
      <div class="panel">
        <div class="section-title">
          <strong>Valor actual</strong>
          <span class="muted">{{ env() }} — {{ valueType() }}</span>
        </div>

        <label class="chk" style="margin-top:0;">
          <input
            type="checkbox"
            [checked]="secretBacked()"
            (change)="secretBacked.set($any($event.target).checked)"
          />
          Este parámetro almacena un secreto
        </label>

        @if (!secretBacked()) {
          <textarea
            id="value"
            class="change-input"
            rows="6"
            spellcheck="false"
            style="max-height:none; min-height:7.5rem; white-space:pre-wrap;"
            [value]="value()"
            (input)="value.set($any($event.target).value)"
          ></textarea>

          <div style="margin-top:1rem;">
            <label for="value-type">Tipo</label>
            <select
              id="value-type"
              style="max-width: 16.25rem;"
              [value]="valueType()"
              (change)="valueType.set($any($event.target).value)"
            >
              <option value="String">String</option>
              <option value="StringList">StringList</option>
              <option value="SecureString">SecureString</option>
            </select>
          </div>
        } @else {
          <div style="display:grid; gap:1rem; margin-top:1rem;">
            <div>
              <label for="secret-name">Nombre del secreto</label>
              <input
                id="secret-name"
                type="text"
                [value]="secretName()"
                (input)="secretName.set($any($event.target).value)"
                placeholder="db-pass-xd-aws"
                spellcheck="false"
                style="max-width: 26.25rem;"
              />
            </div>
            <div>
              <label for="secret-value">Valor del secreto</label>
              <textarea
                id="secret-value"
                class="change-input"
                rows="4"
                spellcheck="false"
                [value]="secretValue()"
                (input)="secretValue.set($any($event.target).value)"
                placeholder="12lj1242&jk4%"
              ></textarea>
            </div>
          </div>
        }

        <div class="actions" style="justify-content:flex-end; margin-top:0.625rem;">
          <button type="button" [disabled]="busy()" (click)="save()">Guardar</button>
        </div>
        @if (resultMsg()) {
          <div class="note {{ resultIsError() ? 'err' : 'ok' }}">{{ resultMsg() }}</div>
        }
      </div>
    }

    <!-- El modal es del guardado, no de "busy": read() también pone busy y antes se
         le mostraba "Guardando…" mientras lo que estaba pasando era una lectura.
         Con saving el rótulo y el botón dicen la verdad, y el panel en línea de
         "Leyendo…" sigue siendo el de la lectura. -->
    <app-busy-modal
      [open]="saving()"
      message="Guardando…"
      [writes]="true"
      (cancelled)="cancelBusy()"
    />
  `,
})
export class ParamsEditPage {
  private readonly envService = inject(EnvironmentService);
  private readonly paramsService = inject(ParamsService);

  readonly environments = signal<EnvironmentInfo[] | null>(null);
  /** `local` no es un ambiente de AWS: esta página solo ofrece los que lo son. */
  readonly awsEnvironments = computed(() => awsEnvironments(this.environments()));
  readonly envs = signal<string[]>([]);
  readonly name = signal('');
  readonly value = signal('');
  readonly valueType = signal('String');
  readonly secretBacked = signal(false);
  readonly secretName = signal('');
  readonly secretValue = signal('');
  readonly loaded = signal(false);

  readonly busy = signal(false);
  /**
   * Lo que está en vuelo es el **guardado**, no una lectura cualquiera.
   *
   * Es lo que abre el modal. Antes era `busy() && loaded()`, que también era
   * cierto al volver a apretar "Leer" sobre un parámetro ya cargado: la lectura
   * se anunciaba como "Guardando…".
   */
  readonly saving = signal(false);
  readonly error = signal<string | null>(null);
  readonly resultMsg = signal<string | null>(null);
  readonly resultIsError = signal(false);

  /**
   * El guardado en vuelo, para que el botón del modal corte la espera.
   *
   * Es la única operación de la página con modal. `writes` va en `true` porque
   * escribe: si se corta la espera, el `put-parameter` puede igual haber salido.
   */
  private readonly op = new CancelSlot();

  /** El ambiente elegido: el array es la fuente de verdad, el string se deriva. */
  readonly env = computed(() => (this.envs().length === 1 ? this.envs()[0] : ''));

  constructor() {
    this.envService.list().then(
      (envs) => this.environments.set(envs.environments),
      (err) => this.error.set('No se pudieron cargar los ambientes: ' + toApiError(err).message),
    );
  }

  read() {
    const env = this.env();
    const name = this.name().trim();
    if (!name) {
      this.error.set('Ingresá el nombre del parámetro.');
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.resultMsg.set(null);
    this.paramsService.get(env, name).then(
      (d) => {
        this.busy.set(false);
        this.value.set(d.value);
        this.valueType.set(d.value_type);
        this.loaded.set(true);
      },
      (err) => {
        this.busy.set(false);
        this.loaded.set(false);
        this.error.set(toApiError(err).message);
      },
    );
  }

  save() {
    const env = this.env();
    const name = this.name().trim();
    if (!name) {
      this.error.set('Ingresá el nombre del parámetro.');
      return;
    }

    const secretBacked = this.secretBacked();
    const secretName = this.secretName().trim();
    const secretValue = this.secretValue().trim();
    if (secretBacked) {
      if (!secretName) {
        this.error.set('Ingresá el nombre del secreto.');
        return;
      }
      if (!secretValue) {
        this.error.set('Ingresá el valor del secreto.');
        return;
      }
    }

    const confirmText = secretBacked
      ? `¿Guardar ${name} en ${env} y crear/actualizar el secreto ${secretName}?\nEl valor del parámetro se escribirá como el nombre del secreto.`
      : `¿Guardar ${name} en ${env}? (put-parameter, overwrite)\nCada región usa su profile/región configurados.`;
    if (!confirm(confirmText)) {
      return;
    }

    this.busy.set(true);
    this.saving.set(true);
    this.error.set(null);
    this.resultMsg.set(null);
    const run = this.op.begin();
    this.paramsService
      .multi(
        {
          name,
          value: secretBacked ? secretName : this.value(),
          value_type: secretBacked ? 'SecureString' : this.valueType(),
          secret_name: secretBacked ? secretName : '',
          secret_value: secretBacked ? secretValue : '',
          envs: [env],
          create_secret: secretBacked,
          dry_run: false,
          confirm: true,
        },
        run,
      )
      .then(
        (d) => {
          if (!this.op.finish(run)) return;
          this.busy.set(false);
          this.saving.set(false);
          const first = d.results[0];
          if (first?.ok) {
            this.resultIsError.set(false);
            this.resultMsg.set(first.message ?? 'Parámetro guardado.');
          } else {
            this.resultIsError.set(true);
            this.resultMsg.set(first?.error || 'No se pudo guardar.');
          }
        },
        (err) => {
          if (!this.op.finish(run)) return;
          this.busy.set(false);
          this.saving.set(false);
          // Cortar la espera no es un fallo: el usuario lo pidió. Ver `core/cancel`.
          if (isCancellation(err)) return;
          this.error.set(toApiError(err).message);
        },
      );
  }

  /**
   * Dejar de esperar el guardado.
   *
   * La caja con el valor editado sigue ahí, lista para volver a apretar Guardar.
   * Lo que se cortó es la espera: si el `put-parameter` ya salió, se aplicó igual
   * y el botón "Dejar de esperar" del modal lo dice.
   */
  cancelBusy(): void {
    if (!this.op.cancel()) return;
    this.busy.set(false);
    this.saving.set(false);
  }
}