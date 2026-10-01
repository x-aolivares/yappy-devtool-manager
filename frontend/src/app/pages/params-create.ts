import { Component, computed, inject, signal } from '@angular/core';
import { EnvironmentInfo, MultiResultInfo, ParamsMultiResponse } from '../api-gen/models';
import { EnvironmentService } from '../core/services/environment.service';
import { ParamsService } from '../core/services/params.service';
import { toApiError } from '../core/services/api-error';
import { StatusBadge } from '../shared/status-badge';
import { CopyButton } from '../shared/copy-button';
import { EnvControlsComponent, ServiceOption } from '../shared/env-controls';

@Component({
  selector: 'app-params-create-page',
  imports: [StatusBadge, CopyButton, EnvControlsComponent],
  template: `
    <h1>Crear / Actualizar en múltiples regiones</h1>
    <p class="muted">
      Un solo valor, varias regiones. En <strong>SSM + secreto asociado</strong> solo pedimos
      <em>3 valores</em>: nombre del parámetro, nombre del secreto y valor del secreto; el valor del
      parámetro se guarda automáticamente igual al nombre del secreto. Cuando no es ese modo,
      el flujo normal es parámetro SSM o secreto aislado.
    </p>

    <div class="panel">
      <div style="margin-bottom:1rem;">
        <app-env-controls
          [environments]="environments()"
          [services]="createServices"
          envLabel="Regiones destino"
          [(envs)]="selectedEnvs"
          [(service)]="serviceMode"
        />
      </div>
      @if (envLoadError()) {
        <span class="muted">No se pudieron cargar los ambientes: {{ envLoadError() }}</span>
      }

      <label for="name">
        {{ serviceMode() === 'secretsmanager' ? 'Nombre del secreto' : 'Nombre del parámetro' }}
      </label>
      <input
        id="name"
        type="text"
        [value]="name()"
        (input)="name.set($any($event.target).value)"
        [placeholder]="serviceMode() === 'secretsmanager' ? '/yappy/dev/secret/db-password' : '/yappy/dev/config'"
        spellcheck="false"
        style="max-width: 26rem;"
      />

      @if (serviceMode() === 'ssm+secret' || (serviceMode() === 'ssm' && createSecret())) {
        <label for="secret-name" style="margin-top:1rem;">Nombre del secreto asociado</label>
        <input
          id="secret-name"
          type="text"
          [value]="secretName()"
          (input)="secretName.set($any($event.target).value)"
          placeholder="db-pass-xd-aws"
          spellcheck="false"
          style="max-width: 26rem;"
        />
      }

      @if (serviceMode() === 'ssm' && !createSecret() || serviceMode() === 'secretsmanager') {
        <label for="value" style="margin-top:1rem;">
          {{ serviceMode() === 'secretsmanager' ? 'Valor del secreto' : 'Valor del parámetro' }}
        </label>
        <textarea
          id="value"
          class="change-input"
          rows="4"
          spellcheck="false"
          [value]="value()"
          (input)="value.set($any($event.target).value)"
          [placeholder]="serviceMode() === 'secretsmanager' ? 'super-secret-value' : 'valor del parámetro'"
        ></textarea>
      }

      @if (serviceMode() === 'ssm+secret' || (serviceMode() === 'ssm' && createSecret())) {
        <label for="secret-value" style="margin-top:1rem;">Valor del secreto</label>
        <textarea
          id="secret-value"
          class="change-input"
          rows="4"
          spellcheck="false"
          [value]="secretValue()"
          (input)="secretValue.set($any($event.target).value)"
          placeholder="12lj1242&jk4%"
        ></textarea>
      }

      @if (serviceMode() === 'ssm') {
        <label class="chk" style="margin-top:1rem;">
          <input
            type="checkbox"
            [checked]="createSecret()"
            (change)="createSecret.set($any($event.target).checked)"
          />
          Este parámetro almacena un secreto
        </label>
      }

      @if (serviceMode() === 'ssm' && !createSecret()) {
        <div style="margin-top:1rem;">
          <label for="value-type">Tipo del parámetro SSM</label>
          <select
            id="value-type"
            style="max-width: 16rem;"
            [disabled]="withSecret()"
            [value]="valueType()"
            (change)="valueType.set($any($event.target).value)"
            title="Con secreto, el parámetro se escribe como SecureString"
          >
            <option value="String">String</option>
            <option value="StringList">StringList</option>
            <option value="SecureString">SecureString</option>
          </select>
        </div>
      }

      <div class="actions" style="justify-content:flex-end; margin-top:0.625rem;">
        <button type="button" class="secondary" [disabled]="busy()" (click)="run(true)">
          Ver comandos
        </button>
        <button type="button" [disabled]="busy()" (click)="run(false)">Crear en las regiones</button>
      </div>
    </div>

    @if (error()) {
      <div class="error-box">{{ error() }}</div>
    }

    @if (busy()) {
      <div class="panel"><span class="spinner"></span>{{ dryRun() ? 'Generando comandos…' : 'Ejecutando…' }}</div>
    }

    @if (result()) {
      @if (result()!.err_count === 0) {
        <div class="ok-box">
          <strong>Listo.</strong> {{ result()!.ok_count }} región{{ result()!.ok_count === 1 ? '' : 'es' }}
          @if (dryRun()) {
            con comando generado
          }
          para {{ result()!.name }}{{ result()!.create_secret ? ' (secreto + parámetro SSM)' : '' }}.
        </div>
      } @else {
        <div class="error-box">
          <strong>{{ result()!.err_count }} región{{ result()!.err_count === 1 ? '' : 'es' }} con error</strong>
          de {{ result()!.results.length }} para {{ result()!.name }}.
        </div>
      }
      <div class="panel">
        <div class="section-title">
          <strong>{{ dryRun() ? 'Comandos por región' : 'Resultado de la ejecución' }}</strong>
        </div>
        <table>
          <thead>
            <tr><th>Región</th><th>Estado</th><th>{{ dryRun() ? 'Comando' : 'Detalle' }}</th></tr>
          </thead>
          <tbody>
            @for (r of result()!.results; track r.env) {
              <tr>
                <td><strong>{{ r.env }}</strong></td>
                <td>
                  @if (r.ok) {
                    <app-badge status="ok" label="OK" />
                  } @else {
                    <app-badge status="error" label="Error" />
                  }
                </td>
                <td>
                  @if (r.ok) {
                    @if (r.script) {
                      <div style="display:flex; flex-direction:column; gap:0.5rem;">
                        <pre>{{ r.script }}</pre>
                        @if (dryRun()) {
                          <span><app-copy-button [text]="r.script" label="Copiar comando" /></span>
                        }
                      </div>
                    } @else {
                      <div class="note ok">{{ r.message ?? '' }}</div>
                    }
                  } @else {
                    <div class="note err">{{ r.error || 'Error desconocido' }}</div>
                  }
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    }
  `,
})
export class ParamsCreatePage {
  private readonly envService = inject(EnvironmentService);
  private readonly paramsService = inject(ParamsService);

  readonly environments = signal<EnvironmentInfo[] | null>(null);
  readonly envLoadError = signal<string | null>(null);
  /**
   * Las píldoras no tienen opción "sin elegir", así que el modo arranca en SSM
   * (el más común) en vez de quedar vacío esperando un `<select>` con placeholder.
   */
  readonly serviceMode = signal<string>('ssm');
  readonly name = signal('');
  readonly value = signal('');
  readonly secretName = signal('');
  readonly secretValue = signal('');
  readonly valueType = signal<string>('String');
  readonly createSecret = signal(false);
  readonly selectedEnvs = signal<string[]>([]);

  /**
   * El servicio acá es el *modo* de escritura, no sólo dónde se guarda: por eso
   * incluye el modo pareado que no existe en el resto de las páginas.
   */
  readonly createServices: readonly ServiceOption[] = [
    { value: 'ssm', label: 'SSM Parameter Store' },
    { value: 'secretsmanager', label: 'Secrets Manager' },
    { value: 'ssm+secret', label: 'SSM + secreto asociado' },
  ];

  readonly busy = signal(false);
  readonly dryRun = signal(false);
  readonly error = signal<string | null>(null);
  readonly result = signal<ParamsMultiResponse | null>(null);

  readonly withSecret = computed(() => this.createSecret() || (this.serviceMode() || 'ssm') === 'ssm+secret');

  constructor() {
    this.envService.list().then(
      (envs) => this.environments.set(envs.environments),
      (err) => this.envLoadError.set(toApiError(err).message),
    );
  }

  private bundle(dryRun: boolean) {
    const mode = this.serviceMode();
    const paramName = this.name().trim();
    const secretName = this.secretName().trim() || paramName;
    const pairedMode = mode === 'ssm+secret' || (mode === 'ssm' && this.createSecret());
    const paramValue = pairedMode ? secretName : this.value();
    const secretValue = this.secretValue() || paramValue;
    const createSecret = pairedMode || mode === 'secretsmanager';
    const effectiveService = mode === 'secretsmanager' ? 'secretsmanager' : pairedMode ? 'ssm+secret' : 'ssm';

    return {
      name: paramName,
      value: paramValue,
      value_type: mode === 'secretsmanager' ? 'SecureString' : pairedMode ? 'SecureString' : this.valueType() || 'String',
      service: effectiveService,
      secret_name: pairedMode || mode === 'secretsmanager' ? secretName : '',
      secret_value: pairedMode || mode === 'secretsmanager' ? secretValue : '',
      envs: this.selectedEnvs(),
      create_secret: createSecret,
      dry_run: dryRun,
      confirm: !dryRun,
    };
  }

  run(dryRun: boolean) {
    const mode = this.serviceMode();
    if (!mode) {
      this.error.set('Elegí el servicio objetivo.');
      return;
    }
    const p = this.bundle(dryRun);
    if (!p.name) {
      this.error.set(mode === 'secretsmanager' ? 'Ingresá el nombre del secreto.' : 'Ingresá el nombre del parámetro.');
      return;
    }
    const pairedMode = mode === 'ssm+secret' || (mode === 'ssm' && this.createSecret());
    if (pairedMode && !this.secretName().trim()) {
      this.error.set('Ingresá el nombre del secreto asociado.');
      return;
    }
    if (pairedMode && !this.secretValue().trim()) {
      this.error.set('Ingresá el valor del secreto asociado.');
      return;
    }
    if (!p.envs.length) {
      this.error.set('Marcá al menos una región destino.');
      return;
    }
    if (!dryRun) {
      const secret = this.withSecret();
      const ops =
        mode === 'secretsmanager'
          ? 'crear/actualizar el secreto en Secrets Manager'
          : secret
            ? 'crear/actualizar el secreto en Secrets Manager Y el parámetro SSM (SecureString)'
            : 'ejecutar put-parameter';
      if (!confirm(`¿${ops} de ${p.name} en: ${p.envs.join(', ')}?\nCada región usa su profile/región configurados.`)) {
        return;
      }
    }

    this.busy.set(true);
    this.dryRun.set(dryRun);
    this.error.set(null);
    this.result.set(null);

    this.paramsService.multi(p).then(
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