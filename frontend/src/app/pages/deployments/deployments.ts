import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { DeploymentsBranchesResponse, DeploymentEnvInfo, EnvironmentInfo } from '../../api-gen/models';
import { DeploymentService } from '../../core/services/deployment.service';
import { EnvironmentService } from '../../core/services/environment.service';
import { toApiError } from '../../core/services/api-error';
import { explainDeploymentFailure, FailureExplained } from '../../core/deployment-help';
import { CancelSlot, isCancellation } from '../../core/cancel';
import { BusyModal } from '../../components/busy-modal/busy-modal';
import { CopyButton } from '../../components/copy-button/copy-button';
import { EnvPicker } from '../../components/env-picker/env-picker';
import { NoticeModal } from '../../components/notice-modal';
import { copyText } from '../../core/copy';

/**
 * Qué rama está desplegada en cada ambiente, para un repo de ECS/Fargate.
 *
 * La respuesta son filas, no un veredicto: el backend camina repo ->
 * parameters.json -> ECS -> CircleCI y cada salto puede fallar por su cuenta.
 * Un ambiente sin desplegar y un ambiente sin token de CircleCI son cosas
 * distintas y la tabla las muestra distintas.
 *
 * Sólo ECS/Fargate. Un proyecto que no sea de ECS tiene otra cadena (y otro
 * nombre de familia) y queda fuera hasta que haya que hacerlo.
 */
@Component({
  selector: 'app-deployments-page',
  imports: [BusyModal, CopyButton, NoticeModal, EnvPicker],
  templateUrl: './deployments.html',
})
export class DeploymentsPage {
  private readonly deployments = inject(DeploymentService);
  private readonly environmentService = inject(EnvironmentService);

  readonly repo = signal('');
  readonly environments = signal<EnvironmentInfo[] | null>(null);
  /** Vacío = todos los que estén en la configuración. Ver `envsToQuery`. */
  readonly envs = signal<string[]>([]);
  readonly busy = signal(false);
  readonly failure = signal<FailureExplained | null>(null);
  readonly copied = signal(false);
  readonly result = signal<DeploymentsBranchesResponse | null>(null);

  /**
   * La consulta en vuelo, para que el botón del modal la corte.
   *
   * La cadena repo → parameters.json → ECS → CircleCI son cuatro saltos y cada
   * uno puede tardar: es de las consultas donde más se echa de menos un
   * "cancelar". Sólo lee, así que el botón dice "Cancelar" y no "Dejar de
   * esperar": no hay nada que el backend pueda llegar a escribir.
   */
  private readonly op = new CancelSlot();

  private copyTimer: ReturnType<typeof setTimeout> | null = null;

  readonly allSelected = computed(
    () =>
      (this.environments()?.length ?? 0) > 0 && this.envs().length === this.environments()!.length,
  );

  constructor() {
    inject(DestroyRef).onDestroy(() => {
      if (this.copyTimer) clearTimeout(this.copyTimer);
    });
    // La lista de ambientes es la de `config/`, sin hardcodear nada. Si falla,
    // el `error-box` del EnvironmentService ya lo dice en la pantalla.
    this.environmentService.list().then((res) => this.environments.set(res.environments));
  }

  toggleAll(): void {
    this.envs.set(this.allSelected() ? [] : (this.environments() ?? []).map((e) => e.env));
  }

  /**
   * Los ambientes a consultar.
   *
   * Vacío significa "todos": es lo que deja el backend y lo que espera la
   * pregunta habitual ("¿qué hay desplegado?"), así que no se obliga a marcar
   * cuatro píldoras para ver algo. Un ambiente desmarcado sí se excluye — que es
   * justo lo que se agrega acá para no pagar el viaje a una región que no se
   * está mirando.
   */
  private envsToQuery(): string[] | undefined {
    const all = (this.environments() ?? []).map((e) => e.env);
    const wanted = this.envs().filter((env) => all.includes(env));
    // Sin nada marcado, o con todo marcado, la decisión es del backend. La lista
    // vacía que queda al filtrar es lo mismo: no se manda un `[]` que el backend
    // leería como "no preguntes nada".
    if (!wanted.length || wanted.length === all.length) return undefined;
    return wanted;
  }

  /**
   * El aviso de una celda abre el mismo modal con el arreglo de ese caso.
   *
   * Sin esto el mensaje de la fila queda como estaba: dice qué falta pero no
   * dónde ponerlo, y el usuario tiene que volver a preguntar.
   */
  explainRow(row: DeploymentEnvInfo): void {
    this.failure.set(explainDeploymentFailure(502, row.message ?? ''));
  }

  async copySnippet(snippet: string): Promise<void> {
    this.copied.set(await copyText(snippet));
    if (this.copyTimer) clearTimeout(this.copyTimer);
    this.copyTimer = setTimeout(() => this.copied.set(false), 1500);
  }

  search(): void {
    const repo = this.repo().trim();
    if (!repo) {
      // No es un fallo del backend: va directo al modal sin pasar por `detail`.
      this.failure.set({
        title: 'Falta el repositorio',
        detail: 'La consulta necesita el nombre del repo en Bitbucket.',
        steps: [
          'Escribí el nombre del repositorio en el campo de arriba.',
          'Va sin el workspace: `yappy-trnxd-backend-payment-aggregator`, no `bg-ti/yappy-...`.',
        ],
      });
      return;
    }
    this.busy.set(true);
    this.failure.set(null);
    const run = this.op.begin();
    this.deployments.branches(repo, this.envsToQuery(), run).then(
      (data) => {
        if (!this.op.finish(run)) return;
        this.busy.set(false);
        this.result.set(data);
      },
      (err) => {
        if (!this.op.finish(run)) return;
        this.busy.set(false);
        // Cortar la espera no es un fallo: el usuario lo pidió. Ver `core/cancel`.
        if (isCancellation(err)) return;
        // El status importa para elegir el arreglo: 405 es "reiniciá el
        // backend", un 502 con el nombre de una variable es "cargá la variable".
        // `toApiError` saca el texto; el status decide qué significa.
        this.failure.set(
          explainDeploymentFailure(
            err instanceof HttpErrorResponse ? err.status : undefined,
            toApiError(err).message,
          ),
        );
        // La tabla vieja es de otro repo: dejarla abajo del aviso de este sería
        // mostrar una respuesta que ya no corresponde a lo que se consultó.
        this.result.set(null);
      },
    );
  }

  /** Cortar la consulta en vuelo y quedarse con lo que ya estaba en pantalla. */
  cancelBusy(): void {
    if (!this.op.cancel()) return;
    this.busy.set(false);
  }
}
