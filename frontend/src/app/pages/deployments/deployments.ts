import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import {
  DeploymentRepoInfo, DeploymentsBranchesResponse, DeploymentEnvInfo, EnvironmentInfo,
} from '../../api-gen/models';
import { DeploymentService } from '../../core/services/deployment.service';
import { matchRepos } from '../../core/repo-filter';
import { EnvironmentService } from '../../core/services/environment.service';
import { toApiError } from '../../core/services/api-error';
import { explainDeploymentFailure, FailureExplained } from '../../core/deployment-help';
import { awsEnvironments } from '../../core/format';
import { CancelSlot, isCancellation } from '../../core/cancel';
import { BusyModal } from '../../components/busy-modal/busy-modal';
import { CopyButton } from '../../components/copy-button/copy-button';
import { EnvPicker } from '../../components/env-picker/env-picker';
import { NoticeModal } from '../../components/notice-modal/notice-modal';
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
  styles: [
    `
      .repo-picker {
        position: relative;
        flex: 1 1 auto;
        min-width: 0;
        display: flex;
      }
      .repo-picker input {
        flex: 1 1 auto;
        min-width: 0;
      }
      .repo-options {
        position: absolute;
        top: calc(100% + 0.25rem);
        left: 0;
        right: 0;
        z-index: 10;
        max-height: 18rem;
        overflow-y: auto;
        margin: 0;
        padding: 0.25rem;
        list-style: none;
        background: var(--sys-elevated);
        border: 0.0625rem solid var(--sys-border);
        border-radius: var(--sys-radius-md);
        box-shadow: var(--sys-shadow-md);
      }
      .repo-options li {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: 0.5rem;
        padding: 0.375rem 0.5rem;
        border-radius: var(--sys-radius-sm);
        cursor: pointer;
      }
      .repo-options li.active {
        background: var(--sys-hover-bg);
      }
    `,
  ],
})
export class DeploymentsPage {
  private readonly deployments = inject(DeploymentService);
  private readonly environmentService = inject(EnvironmentService);

  readonly repo = signal('');
  readonly environments = signal<EnvironmentInfo[] | null>(null);

  // --- el buscador de repos -------------------------------------------------
  //
  // La lista entera de repos del workspace se baja una vez y se filtra en memoria
  // mientras se escribe. Ver `core/repo-filter.ts` para la regla de coincidencia.

  /** Todos los repos del workspace; vacía hasta que la carga termina. */
  readonly catalog = signal<DeploymentRepoInfo[]>([]);
  readonly catalogLoading = signal(false);
  readonly catalogError = signal('');
  /** Cuándo se bajó la lista, para mostrar qué tan vieja es. `null` mientras no hay. */
  readonly catalogFetchedAt = signal<number | null>(null);
  /** Si el desplegable de sugerencias está abierto. */
  readonly open = signal(false);
  /** Posición resaltada con las flechas; `Enter` elige esa. */
  readonly highlight = signal(0);
  /** Lo que el desplegable muestra: los repos que coinciden con lo escrito. */
  readonly suggestions = computed(() => matchRepos(this.catalog(), this.repo()));
  readonly showSuggestions = computed(() => this.open() && this.suggestions().length > 0);

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
    //
    // Sin los locales: esto pregunta por lo desplegado en ECS, y `local` es un
    // ambiente de base de datos —se alcanza por TCP contra el MySQL de la
    // máquina—. Ofrecerme un ambiente contra el que no hay nada desplegado es
    // ofrecer una consulta que no puede responder.
    this.environmentService.list().then((res) =>
      this.environments.set(awsEnvironments(res.environments)),
    );
    void this.loadCatalog();
  }

  /**
   * Baja la lista de repos. `refresh` fuerza la bajada desde Bitbucket.
   *
   * Si falla, la pantalla sigue sirviendo: el campo acepta el nombre a mano y la
   * consulta funciona igual. Sólo se pierde el desplegable.
   */
  async loadCatalog(refresh = false): Promise<void> {
    this.catalogLoading.set(true);
    try {
      const data = await this.deployments.repos(refresh);
      this.catalog.set(data.repos);
      this.catalogFetchedAt.set(data.fetched_at);
      this.catalogError.set('');
    } catch (err) {
      this.catalogError.set(toApiError(err).message);
    } finally {
      this.catalogLoading.set(false);
    }
  }

  /** Lo que escribe el usuario: abre el desplegable y reinicia la selección. */
  onInput(value: string): void {
    this.repo.set(value);
    this.open.set(true);
    this.highlight.set(0);
  }

  /**
   * Teclado en el campo: flechas mueven la selección, `Enter` elige la resaltada
   * (o consulta si no hay desplegable), `Escape` lo cierra.
   */
  onKeydown(event: KeyboardEvent): void {
    const list = this.suggestions();
    if (event.key === 'Escape') {
      this.open.set(false);
      return;
    }
    if (!this.showSuggestions()) {
      if (event.key === 'Enter') this.search();
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      this.highlight.set((this.highlight() + 1) % list.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      this.highlight.set((this.highlight() - 1 + list.length) % list.length);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      this.pick(list[this.highlight()]?.slug ?? this.repo());
    }
  }

  /** Elegir un repo: lo pone en el campo, cierra el desplegable y consulta. */
  pick(slug: string): void {
    this.repo.set(slug);
    this.open.set(false);
    this.search();
  }

  /** Cuándo se bajó la lista, en texto. Vacío mientras no hay lista. */
  catalogAgeText(): string {
    const at = this.catalogFetchedAt();
    return at === null ? '' : new Date(at * 1000).toLocaleTimeString();
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
