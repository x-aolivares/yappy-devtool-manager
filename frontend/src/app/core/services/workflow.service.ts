import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

import { abortCtx } from '../cancel';
import { Api } from '../../api-gen/api';
import {
  getWorkflowState,
  readWorkflowLogs,
} from '../../api-gen/functions';
import {
  WorkflowActionResponse,
  WorkflowCatalogResponse,
  WorkflowStateResponse,
} from '../../api-gen/models';

/** El body que devuelve `GET /api/workflow/logs`. No tipado por el generador. */
export interface LogTail {
  pid: number;
  text: string;
  offset: number;
  alive: boolean;
}

/**
 * Las lecturas y los cinco endpoints con botón de las pantallas de Workflow.
 *
 * Las cuatro páginas de Workflow —Kafka local, Túneles, Servicios y Base de
 * datos— hablan con este servicio y con ningún otro. Comparten el endpoint de
 * estado entero en vez de pedir una fila: una pantalla que preguntara por recurso
 * haría N viajes para contestarse algo que ya tiene en una respuesta.
 *
 * La lectura del estado y la del log son lo que la página refresca, así que
 * devuelven promesas con `AbortSignal`: `resource()` cancela la anterior cuando
 * llega una nueva. Las acciones son una vez cada tanto y van con `firstValueFrom`.
 *
 * Los botones de start y stop no pasan por el cliente generado. No es
 * aesthetic: `ng-openapi-gen` los genera contra el `operation_id` de cada ruta, y
 * para los POST con cuerpo más query el resultado es un archivo que hay que leer
 * para saber si tiene la firma que uno espera. `/api/sessions` ya hizo lo mismo
 * por lo mismo.
 */
@Injectable({ providedIn: 'root' })
export class WorkflowService {
  private readonly api = inject(Api);
  private readonly http = inject(HttpClient);

  /**
   * La foto del estado. Un poll, una request.
   *
   * `env` y `cluster` son opcionales porque hay secciones que no los eligen —Kafka
   * es full local— y mandar el default del cliente en cada poll es ruido en el log
   * del server. Sin ellos, el backend aplica los suyos.
   */
  state(env?: string, cluster?: string, signal?: AbortSignal): Promise<WorkflowStateResponse> {
    return this.api.invoke(getWorkflowState, { env, cluster }, abortCtx(signal));
  }

  /** Lo que se agregó al log desde `offset`. El offset que vuelve es el siguiente. */
  log(
    pid: number,
    offset: number,
    lines = 60,
    signal?: AbortSignal,
  ): Promise<LogTail> {
    return this.api.invoke(
      readWorkflowLogs,
      { pid, lines, offset },
      abortCtx(signal),
    ) as Promise<LogTail>;
  }

  /**
   * Levantar un recurso del registro.
   *
   * `env` opcional por lo mismo que en `state`: Kafka no tiene ambiente. Omitirlo
   * lo deja en el default del backend, que es el mismo que mandaría el cliente.
   */
  startResource(key: string, env?: string): Promise<WorkflowActionResponse> {
    return firstValueFrom(
      this.http.post<WorkflowActionResponse>(
        `${this.api.rootUrl}/api/workflow/resources/start`,
        { key },
        // El objeto se arma acá y no con `{ params: { env } }`: los params de
        // Angular no admiten `undefined` en el tipo, y mandarlo es lo mismo que no
        // mandarlo.
        { params: env ? { env } : {} },
      ),
    );
  }

  startPort(
    port: number,
    label: string,
    env: string,
    cluster: string,
  ): Promise<WorkflowActionResponse> {
    return firstValueFrom(
      this.http.post<WorkflowActionResponse>(
        `${this.api.rootUrl}/api/workflow/ports/start`,
        { port, label },
        { params: { env, cluster } },
      ),
    );
  }

  stop(pid: number, allowForeign = false): Promise<WorkflowActionResponse> {
    return firstValueFrom(
      this.http.post<WorkflowActionResponse>(
        `${this.api.rootUrl}/api/workflow/stop`,
        { pid, allow_foreign: allowForeign },
      ),
    );
  }

  stopAll(allowForeign = false): Promise<WorkflowActionResponse> {
    return firstValueFrom(
      this.http.post<WorkflowActionResponse>(
        `${this.api.rootUrl}/api/workflow/stop-all`,
        {},
        { params: { allow_foreign: allowForeign } },
      ),
    );
  }

  addPort(port: number, label: string, cluster: string): Promise<WorkflowCatalogResponse> {
    return firstValueFrom(
      this.http.post<WorkflowCatalogResponse>(
        `${this.api.rootUrl}/api/workflow/catalog`,
        { port, label },
        { params: { cluster } },
      ),
    );
  }

  renamePort(cluster: string, port: number, label: string): Promise<WorkflowCatalogResponse> {
    return firstValueFrom(
      this.http.patch<WorkflowCatalogResponse>(
        `${this.api.rootUrl}/api/workflow/catalog/${cluster}/${port}`,
        { label },
      ),
    );
  }

  removePort(cluster: string, port: number): Promise<WorkflowCatalogResponse> {
    return firstValueFrom(
      this.http.delete<WorkflowCatalogResponse>(
        `${this.api.rootUrl}/api/workflow/catalog/${cluster}/${port}`,
      ),
    );
  }

  }