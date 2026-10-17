import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, firstValueFrom } from 'rxjs';

import { abortCtx } from '../cancel';
import { Api } from '../../api-gen/api';
import { readWorkflowLogs } from '../../api-gen/functions';
import {
  WorkflowActionResponse,
  WorkflowCatalogResponse,
  WorkflowStateResponse,
} from '../../api-gen/models';

/** Con qué ambiente y cluster se abre el stream. Ambos opcionales. */
export interface WorkflowScope {
  readonly env?: string;
  readonly cluster?: string;
}

/**
 * Lo que el stream puede decir.
 *
 * Dos eventos y no un `error` de RxJS, porque son dos cosas distintas para la
 * pantalla: llegó un estado nuevo, o se cortó la conexión **por ahora**. El
 * `EventSource` reconecta solo, así que el corte no es un fallo — es la forma en
 * que se informa de que el backend se apagó un segundo.
 */
export type WorkflowStreamEvent =
  | { readonly kind: 'state'; readonly state: WorkflowStateResponse }
  | { readonly kind: 'error'; readonly detail: string };

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
 * La lectura del estado llega empujada por `stream()` y la del log se sigue
 * leyendo por `offset`: son dos problemas distintos. El log es un archivo que
 * crece, así que "qué hay desde el byte N" es una pregunta de cursor. El estado es
 * una foto de un conjunto de procesos, así que conviene que se avise cuándo cambia
 * en vez de ir a buscarlo cada 1,5s para descubrir que no.
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
   * El estado, empujado: se abre una vez y se escucha.
   *
   * Esto reemplaza al poll de 1,5s. La diferencia no es de transporte sino de
   * pregunta: la pantalla ya no interroga al backend cada 1,5s si el túnel terminó,
   * espera a que el backend le diga que terminó. Un arranque que tarda 30s son
   * treinta actualizaciones de una fila, no veinte requests que vuelven diciendo
   * "todavía no".
   *
   * `EventSource` reconecta solo cuando el server se cae y se levanta, así que un
   * `error` acá es "se cortó ahora", no "se rompió". El primer frame después de
   * reconectar trae el estado entero, y no hace falta pedir nada para recuperarlo.
   *
   * El teardown del `Observable` cierra la conexión: desarmar el `unsubscribe` es lo
   * que hace que abandonar la ruta no deje un stream abierto en el server.
   */
  stream(scope: WorkflowScope): Observable<WorkflowStreamEvent> {
    return new Observable<WorkflowStreamEvent>((subscriber) => {
      const params = new URLSearchParams();
      if (scope.env) params.set('env', scope.env);
      if (scope.cluster) params.set('cluster', scope.cluster);
      const query = params.toString();
      const source = new EventSource(
        `${this.api.rootUrl}/api/workflow/stream${query ? `?${query}` : ''}`,
      );

      source.onmessage = (event: MessageEvent<string>) => {
        try {
          subscriber.next({ kind: 'state', state: JSON.parse(event.data) as WorkflowStateResponse });
        } catch {
          subscriber.next({
            kind: 'error',
            detail: 'El servidor mandó un estado que no se pudo leer.',
          });
        }
      };
      source.onerror = () => {
        subscriber.next({ kind: 'error', detail: 'No se pudo conectar con el servidor.' });
      };

      return () => source.close();
    });
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