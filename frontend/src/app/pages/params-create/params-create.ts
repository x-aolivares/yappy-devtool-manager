import { Component, computed, inject, signal } from '@angular/core';
import { EnvironmentInfo, MultiResultInfo, ParamsMultiResponse } from '../../api-gen/models';
import { EnvironmentService } from '../../core/services/environment.service';
import { ParamsService } from '../../core/services/params.service';
import { toApiError } from '../../core/services/api-error';
import { CancelSlot, isCancellation } from '../../core/cancel';
import { awsEnvironments } from '../../core/format';
import { BusyModal } from '../../components/busy-modal/busy-modal';
import { StatusBadge } from '../../components/status-badge/status-badge';
import { CopyButton } from '../../components/copy-button/copy-button';
import { EnvControls, ServiceOption } from '../../components/env-controls/env-controls';
import { PaginationBar } from '../../components/pagination-bar/pagination-bar';
import { paginate } from '../../core/paginate';

@Component({
  selector: 'app-params-create-page',
  imports: [
    StatusBadge,
    BusyModal,
    CopyButton,
    EnvControls,
    PaginationBar,
  ],
  templateUrl: './params-create.html',
})
export class ParamsCreatePage {
  private readonly envService = inject(EnvironmentService);
  private readonly paramsService = inject(ParamsService);

  readonly environments = signal<EnvironmentInfo[] | null>(null);
  /** `local` no es un ambiente de AWS: esta página solo ofrece los que lo son. */
  readonly awsEnvironments = computed(() => awsEnvironments(this.environments()));
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

  /**
   * La escritura en vuelo, para que el botón del modal corte la espera.
   *
   * "Ver comandos" no escribe nada y "Crear en las regiones" escribe en todas
   * las marcadas: el modal usa `dryRun` para no llamarlo "Cancelar" en el
   * segundo caso.
   */
  private readonly op = new CancelSlot();

  readonly withSecret = computed(() => this.createSecret() || (this.serviceMode() || 'ssm') === 'ssm+secret');

  /**
   * Una fila por región, paginada.
   *
   * Con dos o tres regiones la barra nunca aparece —el control se oculta solo— y
   * con una pantalla completa de regiones es lo que evita que haya que scrollear
   * para leer un error del último `AWS_REGION` que se mandó.
   */
  readonly page = paginate(() => this.result()?.results ?? []);

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

    const run = this.op.begin();
    this.paramsService.multi(p, run).then(
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

  /** Dejar de esperar el comando o la creación en vuelo. Ver `core/cancel`. */
  cancelBusy(): void {
    if (!this.op.cancel()) return;
    this.busy.set(false);
  }
}