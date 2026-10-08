import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { DeploymentsBranchesResponse, DeploymentEnvInfo, EnvironmentInfo } from '../api-gen/models';
import { DeploymentService } from '../core/services/deployment.service';
import { EnvironmentService } from '../core/services/environment.service';
import { toApiError } from '../core/services/api-error';
import { explainDeploymentFailure, FailureExplained } from '../core/deployment-help';
import { CancelSlot, isCancellation } from '../core/cancel';
import { BusyModalComponent } from '../shared/busy-modal';
import { CopyButton } from '../shared/copy-button';
import { EnvPickerComponent } from '../shared/env-picker';
import { NoticeModalComponent } from '../shared/notice-modal';
import { copyText } from '../core/copy';

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
  imports: [BusyModalComponent, CopyButton, NoticeModalComponent, EnvPickerComponent],
  template: `
    <h1>Rama desplegada por ambiente</h1>
    <p class="muted">
      Para un repositorio de ECS/Fargate: qué rama está desplegada en cada ambiente. La cadena va
      del <code>parameters.json</code> del repo al task definition de ECS, y de ahí al pipeline de
      CircleCI que compiló la imagen.
    </p>

    <div class="panel">
      <div class="field">
        <label class="field-label" for="repo">Repositorio</label>
        <div class="field-row">
          <input
            id="repo"
            type="text"
            [value]="repo()"
            (input)="repo.set($any($event.target).value)"
            (keydown.enter)="search()"
            placeholder="yappy-trnxd-backend-payment-aggregator"
            spellcheck="false"
          />
          <button type="button" [disabled]="busy() || !repo().trim()" (click)="search()">
            Consultar
          </button>
        </div>
      </div>

      <div class="field">
        <div class="field-label-row">
          <span class="field-label">Ambientes</span>
          <button type="button" class="linkish" (click)="toggleAll()">
            {{ allSelected() ? 'Ninguno' : 'Todos' }}
          </button>
        </div>
        <app-env-picker
          [environments]="environments()"
          [(selected)]="envs"
          label="Ambientes a consultar"
        />
        <p class="muted hint">
          @if (allSelected()) {
            Se consultan los {{ environments()?.length ?? 0 }} ambientes de la configuración.
          } @else if (envs().length) {
            Se consultan {{ envs().length }} de {{ environments()?.length ?? 0 }} ambientes.
          } @else {
            Sin marcar: se consultan todos los que estén en la configuración.
          }
        </p>
      </div>
    </div>

    <app-busy-modal [open]="busy()" message="Consultando…" (cancelled)="cancelBusy()" />

    @if (failure(); as fail) {
      <app-notice-modal [open]="true" [title]="fail.title" (closed)="failure.set(null)">
        <p class="muted">{{ fail.detail }}</p>
        @if (fail.steps.length) {
          <ol class="notice-steps">
            @for (step of fail.steps; track step) {
              <li>{{ step }}</li>
            }
          </ol>
        }
        @if (fail.links?.length) {
          <p class="notice-links-label">Para conseguir el token:</p>
          <ul class="notice-links">
            @for (link of fail.links; track link.href) {
              <li>
                <a [href]="link.href" target="_blank" rel="noopener noreferrer">{{ link.label }}</a>
              </li>
            }
          </ul>
        }
        @if (fail.snippet) {
          <pre class="notice-snippet">{{ fail.snippet }}</pre>
          <button type="button" class="secondary" (click)="copySnippet(fail.snippet)">
            {{ copied() ? 'Copiado' : 'Copiar para pegar' }}
          </button>
        }
      </app-notice-modal>
    }

    @if (result(); as data) {
      <div class="panel">
        <div class="section-title">
          <strong>{{ data.service_name }}</strong>
          <span class="muted">
            grupo {{ data.group_name }}
            @if (data.task_definition_type) {
              · {{ data.task_definition_type }}
            }
            @if (data.cluster) {
              · cluster {{ data.cluster }}
            }
          </span>
        </div>

        @if (!data.environments.length) {
          <p class="muted">No hay ambientes de AWS en la configuración.</p>
        } @else {
          <table class="table">
            <thead>
              <tr>
                <th>Ambiente</th>
                <th>Región</th>
                <th>Rama desplegada</th>
                <th>Pipeline</th>
                <th>Task def</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              @for (row of data.environments; track row.env) {
                <tr>
                  <td>
                    <strong>{{ row.env.toUpperCase() }}</strong>
                  </td>
                  <td class="muted">{{ row.region ?? '—' }}</td>
                  <td>
                    @if (row.branch) {
                      <code class="branch">{{ row.branch }}</code>
                      @if (row.commit_subject) {
                        <div class="muted note" [title]="row.commit_subject">
                          {{ row.commit_subject }}
                        </div>
                      }
                      <div class="row-meta">
                        @if (row.tag) {
                          <span class="tag-chip" [title]="'Deploy tag: ' + row.tag">
                            {{ row.tag }}
                          </span>
                        }
                        @if (row.commit) {
                          <span class="muted mono">{{ row.commit.slice(0, 7) }}</span>
                        }
                      </div>
                    } @else {
                      <span class="muted">—</span>
                    }
                    @if (row.message) {
                      <div class="muted note">
                        {{ row.message }}
                        @if (row.status === 'circleci_unavailable') {
                          <button type="button" class="linkish" (click)="explainRow(row)">
                            Cómo arreglarlo
                          </button>
                        }
                      </div>
                    }
                  </td>
                  <td>
                    @if (row.pipeline_id != null) {
                      <span class="mono">{{ row.pipeline_id }}</span>
                      @if (row.branch_from_pipeline != null) {
                        <div class="muted note">
                          desde <span class="mono">{{ row.branch_from_pipeline }}</span>
                        </div>
                      }
                    } @else {
                      <span class="muted">—</span>
                    }
                  </td>
                  <td class="muted">
                    @if (row.task_definition_revision != null) {
                      rev {{ row.task_definition_revision }}
                    } @else {
                      —
                    }
                  </td>
                  <td class="row-actions">
                    @if (row.branch) {
                      <app-copy-button [text]="row.branch" label="Rama" />
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
          <p class="muted note">
            Para un ambiente que no aparece desplegado, o para el detalle completo de una fila, mirá
            el task definition y su imagen en ECR.
          </p>
        }
      </div>
    }
  `,
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
