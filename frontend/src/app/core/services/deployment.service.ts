import { DOCUMENT } from '@angular/common';
import { Injectable, inject } from '@angular/core';
import { Api } from '../../api-gen/api';
import { listDeploymentBranches, listDeploymentRepos } from '../../api-gen/functions';
import { DeploymentsBranchesRequest, DeploymentsBranchesResponse, DeploymentReposResponse } from '../../api-gen/models';
import { abortCtx } from '../cancel';

/**
 * La copia del catálogo en `localStorage`.
 *
 * No decide nada: el TTL y el refresh son del backend (`repo_catalog.py`), que
 * es el que tiene la lista fresca. Esta copia sirve para dos cosas —pintar las
 * sugerencias sin esperar el primer request, y seguir ofreciéndolas si el
 * backend no está—, y por eso se guarda y se lee con la misma forma que
 * devuelve la API.
 */
const REPOS_STORAGE_KEY = 'yappy.deployment-repos.v1';

/**
 * La pantalla "Rama desplegada" consume el cliente generado como las demás, para
 * que el contrato con el backend siga teniendo un solo lugar donde está escrito.
 */
@Injectable({ providedIn: 'root' })
export class DeploymentService {
  private readonly api = inject(Api);
  private readonly document = inject(DOCUMENT);

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

  /**
   * Los repos del workspace, para el input.
   *
   * `refresh=true` es el botón "Actualizar": le pide al backend que pregunte de
   * nuevo a Bitbucket aunque la caché esté fresca, que es lo único que hace
   * visible un repo creado hace cinco minutos.
   *
   * La respuesta se copia a `localStorage` siempre, también cuando vino de la
   * caché del backend: lo que se guarda es "lo último que se supo", y sirve de
   * respaldo si la próxima carga no llega.
   */
  repos(refresh = false): Promise<DeploymentReposResponse> {
    return this.api.invoke(listDeploymentRepos, { refresh }).then((res) => {
      this.storeRepos(res);
      return res;
    });
  }

  /**
   * La copia guardada, o `null` si nunca se guardó o quedó ilegible.
   *
   * Un JSON que no tiene la forma esperada cuenta como `null`: es una caché
   * rota, no un error que valga romper la pantalla.
   */
  cachedRepos(): DeploymentReposResponse | null {
    try {
      const raw = this.document.defaultView?.localStorage.getItem(REPOS_STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as DeploymentReposResponse;
      if (!parsed || typeof parsed.workspace !== 'string' || !Array.isArray(parsed.repositories)) {
        return null;
      }
      return parsed;
    } catch {
      return null;
    }
  }

  private storeRepos(res: DeploymentReposResponse): void {
    try {
      this.document.defaultView?.localStorage.setItem(REPOS_STORAGE_KEY, JSON.stringify(res));
    } catch {
      // Sin espacio en el navegador el catálogo igual funciona: sólo se pierde
      // el pintado instantáneo de la próxima carga.
    }
  }
}
