import { Injectable, inject } from '@angular/core';
import { Api } from '../../api-gen/api';
import { listDeploymentBranches } from '../../api-gen/functions';
import { DeploymentsBranchesRequest, DeploymentsBranchesResponse } from '../../api-gen/models';
import { abortCtx } from '../cancel';

/**
 * La pantalla "Rama desplegada" consume el cliente generado como las demás, para
 * que el contrato con el backend siga teniendo un solo lugar donde está escrito.
 */
@Injectable({ providedIn: 'root' })
export class DeploymentService {
  private readonly api = inject(Api);

  /**
   * `envs` limita la consulta. `undefined` deja la decisión en el backend, que
   * usa todos los ambientes que están en la configuración — es lo que espera la
   * pregunta habitual y evita obligar a marcar cuatro píldoras para ver algo.
   *
   * `signal` es la del botón de cancelar del modal de espera: la cadena
   * repo → ECS → CircleCI es larga y hay veces que uno se arrepiente del click a
   * mitad de camino.
   */
  branches(repo: string, envs?: string[], signal?: AbortSignal): Promise<DeploymentsBranchesResponse> {
    // El tipo generado no acepta `undefined` en un campo opcional, así que se
    // manda la lista o `null` — no un `undefined` que el serializador omitiría
    // de una forma distinta según cómo se arme el body.
    return this.api.invoke(
      listDeploymentBranches,
      { body: { repo, envs: envs ?? null } as DeploymentsBranchesRequest },
      abortCtx(signal),
    );
  }
}
