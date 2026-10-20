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
 * Lo que el stream del estado puede decir.
 *
 * Dos eventos y no un `error` de RxJS, porque son dos cosas distintas para la
 * pantalla: llegó un estado nuevo, o se cortó la conexión **por ahora**. El
 * `EventSource` reconecta solo, así que el corte no es un fallo — es la forma en
 * que se informa de que el backend se apagó un segundo.
 */
export type WorkflowStreamEvent =
  | { readonly kind: 'state'; readonly state: WorkflowStateResponse }
  | { readonly kind: 'error'; readonly detail: string };

/**
 * Un frame del log.
 *
 * `gone` es un evento y no un error: el server lo dice antes de cerrar la conexión,
 * así que el panel no lo descubre con un 404 en su próxima consulta — que es una
 * request más para enterarse de algo que ya estaba escrito.
 */
export interface LogStreamEvent {
  readonly pid: number;
  readonly text: string;
  readonly offset: number;
  readonly alive: boolean;
  readonly gone: boolean;
  /** El server manda esto cuando el stream se cae por dentro. */
  readonly error?: string;
}

/** El body que devuelve `GET /api/workflow/logs`. No tipado por el generador. */
export interface LogTail {
  pid: number;
  text: string;
  offset: number;
  alive: boolean;
}

/**
 * Las dos lecturas de las pantallas de Workflow, y los cinco endpoints con botón.
 *
 * Las cuatro páginas —Kafka local, Túneles, Servicios y Base de datos— hablan con
 * este servicio y con ningún otro.
 *
 * **Las dos lecturas se empujan, y son problemas distintos.** El estado es una foto
 * de un conjunto de procesos: el backend publica cuando cambia y la pantalla
 * escucha. El log es un archivo que crece, y el cursor vive en la conexión —el
 * server recuerda hasta dónde leyó—, así que el browser no vuelve a mandar "seguí
 * desde el byte 3.433.943". Los dos reemplazan a un poll de 1,5s que, en el caso
 * del log, gastaba 40 requests por minuto para recibir un offset idéntico.
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
   * La diferencia con el poll no es de transporte sino de pregunta: la pantalla ya
   * no interroga al backend cada 1,5s si el túnel terminó, espera a que el backend
   * le diga que terminó. Un arranque que tarda 30s son treinta actualizaciones de
   * una fila, no veinte requests que vuelven diciendo "todavía no".
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
          const payload = JSON.parse(event.data) as WorkflowStateResponse & { error?: string };
          // El server manda `{"error": "..."}` cuando el stream se cae por
          // dentro —el generator ya no puede responder con un status code—.
          // Sin esto, el cliente veía una conexión abierta que no decía nada
          // y no podía distinguirla de "no está pasando nada".
          if (payload && typeof payload.error === 'string') {
            subscriber.next({ kind: 'error', detail: payload.error });
            return;
          }
          subscriber.next({ kind: 'state', state: payload as WorkflowStateResponse });
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

  /**
   * El log de un proceso, empujado.
   *
   * El primer frame trae la cola del archivo —el mismo contrato que el poll con
   * `offset=0`—, y después sólo lo que se agregó. El cursor vive en la conexión, así
   * que el panel no guarda offset ni lo manda de vuelta.
   */
  streamLogs(pid: number, section = 'workflow'): Observable<LogStreamEvent> {
    return new Observable<LogStreamEvent>((subscriber) => {
      const params = new URLSearchParams({ pid: String(pid), page: section });
      const source = new EventSource(
        `${this.api.rootUrl}/api/workflow/stream/logs?${params.toString()}`,
      );

      source.onmessage = (event: MessageEvent<string>) => {
        try {
          const payload = JSON.parse(event.data) as LogStreamEvent & { error?: string };
          if (payload && typeof payload.error === 'string') {
            subscriber.next({ ...payload, error: payload.error });
            return;
          }
          subscriber.next(payload);
        } catch {
          subscriber.error(new Error('El log llegó ilegible.'));
        }
      };
      source.onerror = () => {
        // El `EventSource` reconecta solo. Un corte es "se cortó ahora", y el
        // siguiente frame trae el log desde el offset del server, que es el que
        // importa: sin él, un corte perdería las líneas que se escribieron durante.
        subscriber.next({ pid, text: '', offset: 0, alive: true, gone: false });
      };

      return () => source.close();
    });
  }

  /**
   * El log por cursor, en una request.
   *
   * Queda junto al stream porque el endpoint sigue existiendo: es la forma
   * documentada, la que sirve para un `curl` o un script, y la que un test de
   * contrato puede llamar sin abrir una conexión.
   */
  log(
    pid: number,
    offset: number,
    lines = 60,
    signal?: AbortSignal,
    section = 'workflow',
  ): Promise<LogTail> {
    return this.api.invoke(
      readWorkflowLogs,
      // `page` no cambia la respuesta: existe para que la línea del access log
      // diga qué panel preguntó. Ver `WorkflowLogs.section`.
      { pid, lines, offset, page: section },
      abortCtx(signal),
    ) as Promise<LogTail>;
  }

  /**
   * Levantar un recurso del registro.
   *
   * `env` opcional por lo mismo que en `stream()`: Kafka no tiene ambiente.
   * Omitirlo ya no cae en un ambiente concreto del backend: `env` vacío es *sin
   * ambiente*, que es la config con la que el broker se levanta de verdad.
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

  /**
   * Wipe and re-format the local Kafka storage.
   *
   * Deletes every local topic. The screen only offers it when a failed start's own
   * log says this is the fix, and asks first — but the ask lives on the screen, not
   * here, because what it destroys is a decision a person makes.
   */
  cleanKafkaStorage(): Promise<WorkflowActionResponse> {
    return firstValueFrom(
      this.http.post<WorkflowActionResponse>(
        `${this.api.rootUrl}/api/workflow/kafka/clean`,
        {},
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