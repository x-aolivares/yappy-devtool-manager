/**
 * Los dobles de prueba de Workflow.
 *
 * Las cuatro páginas de Workflow hablan con los mismos dos servicios —el estado y
 * el log— y sus specs necesitan el mismo andamiaje para responder. Con una sola
 * pantalla eran 800 líneas en un archivo; con cuatro serían las mismas 800 líneas
 * repetidas cuatro veces, y el día que el fake se desactualice contra la firma
 * real lo van a desactualizar en cuatro lugares.
 *
 * **Este archivo es sólo para tests.** No lo importa nada fuera de un
 * `*.spec.ts`, así que el tree-shaking lo saca del bundle.
 *
 * Los fakes no inventan comportamiento: cada método responde `ok` y anota la
 * llamada, para que el test afirme sobre lo que la página *pidió* y no sobre lo
 * que el backend *haría*.
 *
 * El del estado es un stream y no una función: se suscribe, y el test le empuja
 * frames con `emiteEstado`. Es la diferencia entre "el backend respondió esto" y
 * "el backend dijo que algo pasó", y un test que no puede empujar un frame no puede
 * probar que la pantalla reacciona a un cambio.
 */
import { HttpErrorResponse } from '@angular/common/http';
import type { Provider } from '@angular/core';
import { Observable, Subject, finalize } from 'rxjs';

import type {
  EnvironmentInfo, WorkflowPortInfo, WorkflowResourceInfo, WorkflowStateResponse,
} from '../api-gen/models';
import { EnvironmentService } from './services/environment.service';
import {
  LogStreamEvent, WorkflowScope, WorkflowService, WorkflowStreamEvent,
} from './services/workflow.service';

export const ENVS = [
  { env: 'dev', region: 'us-west-2', profile: 'base-profile' },
  { env: 'qa', region: 'us-west-1', profile: 'base-profile' },
];

/** Una fila del catálogo de túneles. */
export function fila(over: Partial<WorkflowPortInfo> = {}): WorkflowPortInfo {
  return {
    cluster: 'cap',
    port: 8402,
    label: 'core-p2m-m2p',
    state: 'down',
    pid: null,
    since: null,
    foreign: false,
    browser_url: null,
    log_file: '',
    conflict: null,
    ...over,
  };
}

/** Una tarjeta: Kafka, un servicio con destino propio o la base de datos. */
export function recurso(over: Partial<WorkflowResourceInfo> = {}): WorkflowResourceInfo {
  return {
    key: 'bastion',
    group: 'named',
    name: 'Bastion',
    detail: 'Documento distinto',
    state: 'down',
    remote_port: 9091,
    local_port: 3000,
    pid: null,
    since: null,
    foreign: false,
    browser_url: null,
    log_file: '',
    conflict: null,
    fix: '',
    ...over,
  };
}

/** Una fila de Kafka con el storage bloqueado: el estado que el backend señala. */
export function kafkaBloqueada(over: Partial<WorkflowResourceInfo> = {}): WorkflowResourceInfo {
  return recurso({
    key: 'kafka-server',
    group: 'kafka',
    name: 'Kafka Server',
    detail: 'KRaft · storage en devkit/kafka',
    state: 'error',
    remote_port: 9092,
    local_port: 9092,
    fix: 'kafka_clean_storage',
    ...over,
  });
}

/** Un Kafka arriba y con archivo de log: la fila que los tests de logs usan. */
export function kafkaArriba(over: Partial<WorkflowResourceInfo> = {}): WorkflowResourceInfo {
  return recurso({
    key: 'kafka-ui',
    group: 'kafka',
    name: 'Kafdrop UI',
    state: 'up',
    pid: 21224,
    log_file: 'kafka-ui-21224.log',
    ...over,
  });
}

/** La respuesta del endpoint de estado, con las tres listas vacías. */
export function estado(over: Partial<WorkflowStateResponse> = {}): WorkflowStateResponse {
  return {
    cluster: 'cap',
    env: 'dev',
    generated_at: 1757000000,
    remote_host: 'nlbcapabilities.dev.yappycloud.com',
    is_local: false,
    resources: [],
    ports: [],
    unattributed: [],
    ...over,
  };
}

/** El caso del server: el pid dejó de estar trackeado y el backend 404ea. */
export function pidSinTrackear(detail = 'No hay ningún proceso trackeado con ese pid.'): HttpErrorResponse {
  return new HttpErrorResponse({ status: 404, error: { detail } });
}

/** Un corte de red: status 0, que el panel tiene que aguantar sin cerrarse. */
export function corteDeRed(): HttpErrorResponse {
  return new HttpErrorResponse({ status: 0, error: new ProgressEvent('error') });
}

/**
 * Un stream abierto, que el test empuja.
 *
 * Guarda el ámbito con el que se abrió y si el front lo cerró: esas son las dos
 * cosas que un test necesita afirmar y que no se pueden ver mirando la pantalla.
 */
export class WorkflowStreamDouble {
  /** Los frames que salieron, para poder afirmar que no salió alguno. */
  readonly frames: WorkflowStreamEvent[] = [];


  private readonly subject = new Subject<WorkflowStreamEvent>();
  private isClosed = false;

  /** Con qué ambiente y cluster se abrió. Sin los dos = una página sin ámbito. */
  constructor(readonly scope: WorkflowScope) {}

  /** Empuja un frame, como el backend cuando algo se mueve. */
  emit(event: WorkflowStreamEvent): void {
    this.frames.push(event);
    this.subject.next(event);
  }

  /**
   * `true` cuando la página desarmó la suscripción.
   *
   * Sale del `finalize`, porque desarmar es lo que cierra el `EventSource` del otro
   * lado: un test que no la desarma está probando una página que se va a dejar
   * conexiones abiertas en el server.
   */
  get closed(): boolean {
    return this.isClosed;
  }

  /** Lo que la página se suscribe. */
  observable(): Observable<WorkflowStreamEvent> {
    return this.subject
      .asObservable()
      .pipe(finalize(() => { this.isClosed = true; }));
  }
}

/** Todo lo que los fakes anotan, para que el test afirme sobre las llamadas. */
export interface WorkflowCalls {
  /** Lo que el próximo frame va a decir. Cambiarlo y empujar es un cambio de estado. */
  answer: WorkflowStateResponse;
  /** Un stream por cada suscripción abierta, en orden de apertura. */
  streams: WorkflowStreamDouble[];
  startedPorts: Array<{ port: number; label: string; cluster: string }>;
  startedKeys: string[];
  /** El ambiente de cada `startedKeys`, en el mismo orden. `undefined` = no mandó. */
  startEnvs: Array<string | undefined>;
  stopped: Array<{ pid: number; allowForeign: boolean }>;
  stopAllCalls: boolean[];
  /** Cuántas veces se pidió limpiar el storage de Kafka, y si falló. */
  kafkaCleanCalls: number;
  kafkaCleanError: Error | null;
  addedPorts: Array<{ port: number; cluster: string }>;
  renames: Array<{ cluster: string; port: number; label: string }>;
  removed: Array<{ cluster: string; port: number }>;
  /** Cuando no es `null`, el próximo start tira lo que esta función devuelva. */
  failStart: ((err: Error) => Error) | null;
  /** Lo que `/api/workflow/logs` rechaza. `null` = responde bien. */
  logError: HttpErrorResponse | null;
  logCalls: Array<{ pid: number; offset: number; section: string }>;
  /** El texto que trae el primer frame del log, el que el server manda al abrir. */
  logText: string;
  /** Un stream de log por panel abierto, en orden de apertura. */
  logStreams: WorkflowLogStreamDouble[];
  /** Cuántas veces se pidió la lista de ambientes. Una sección sin selector, cero. */
  envCalls: number;
  /**
   * Los ambientes que la lista devuelve.
   *
   * Cambiable por test: es lo que decide con qué ambiente se abre el stream, y
   * una máquina puede no tener el que la página trae por defecto.
   */
  envs: EnvironmentInfo[];
}

/** Un `WorkflowCalls` limpio. Cada spec tiene el suyo en una variable propia. */
export function callsVacios(): WorkflowCalls {
  return {
    answer: estado(),
    streams: [],
    startedPorts: [],
    startedKeys: [],
    startEnvs: [],
    stopped: [],
    stopAllCalls: [],
    kafkaCleanCalls: 0,
    kafkaCleanError: null,
    addedPorts: [],
    renames: [],
    removed: [],
    failStart: null,
    logError: null,
    logCalls: [],
    logText: '',
    logStreams: [],
    envCalls: 0,
    envs: ENVS,
  };
}

/**
 * Empuja un estado a todos los streams abiertos.
 *
 * Es lo que hace el backend cuando el supervisor escribe un veredicto: el mismo
 * `answer` que el test tenía preparado, ahora como frame.
 */
export function emiteEstado(
  calls: WorkflowCalls,
  over: Partial<WorkflowStateResponse> = {},
): WorkflowCalls {
  calls.answer = { ...calls.answer, ...over };
  for (const stream of calls.streams) {
    stream.emit({ kind: 'state', state: calls.answer });
  }
  return calls;
}

/**
 * Un stream de log abierto, que el test empuja.
 *
 * Mismo criterio que el del estado: lo que el server manda se empuja como frame, y
 * no hay forma de escribir un test sobre "el panel cambió cuando el log creció" si
 * no se puede hacer crecer el log.
 */
export class WorkflowLogStreamDouble {
  readonly frames: LogStreamEvent[] = [];

  private readonly subject = new Subject<LogStreamEvent>();
  private isClosed = false;
  /** El offset que el server llevaría; el doble lo usa para no mandar el mismo texto dos veces. */
  private cursor = 0;

  constructor(readonly pid: number, readonly section = 'workflow') {}

  emit(frame: LogStreamEvent): void {
    this.cursor = Math.max(this.cursor, frame.text.length);
    this.frames.push(frame);
    this.subject.next(frame);
  }

  get closed(): boolean {
    return this.isClosed;
  }

  observable(): Observable<LogStreamEvent> {
    return this.subject.asObservable().pipe(finalize(() => { this.isClosed = true; }));
  }

  /** Dónde quedó el cursor, para que un test pueda mandar "lo que se agregó". */
  get offset(): number {
    return this.cursor;
  }
}

/** Empuja texto nuevo al panel, como cuando el archivo del proceso crece. */
export function emiteLog(calls: WorkflowCalls, text: string): WorkflowCalls {
  for (const stream of calls.logStreams) {
    stream.emit({
      pid: stream.pid,
      text,
      offset: stream.offset + text.length,
      alive: true,
      gone: false,
    });
  }
  return calls;
}

/**
 * Empuja el `gone`: el pid dejó de estar trackeado.
 *
 * Antes esto lo descubría el panel con un 404 en su próxima consulta — una request
 * más para enterarse de algo que ya estaba escrito—. Ahora es un frame.
 */
export function emiteLogGone(calls: WorkflowCalls, pid?: number): WorkflowCalls {
  for (const stream of calls.logStreams) {
    if (pid !== undefined && stream.pid !== pid) continue;
    stream.emit({ pid: stream.pid, text: '', offset: stream.offset, alive: false, gone: true });
  }
  return calls;
}
/** Empuja el corte de conexión: lo que el `EventSource` emite cuando se cae. */
export function emiteCaida(
  calls: WorkflowCalls,
  detail = 'No se pudo conectar con el servidor.',
): WorkflowCalls {
  for (const stream of calls.streams) {
    stream.emit({ kind: 'error', detail });
  }
  return calls;
}

/**
 * Los providers de `TestBed` para las cuatro páginas y para los componentes
 * compartidos.
 *
 * Se pasa el mismo objeto `calls` que el spec tiene en una variable, así que
 * cambiar `calls.answer` a mitad de un test y pedir un `reload()` hace que la
 * próxima vuelta traiga otra cosa — que es como se prueba un cambio de estado.
 */
export function workflowProviders(calls: WorkflowCalls): Provider[] {
  return [
    {
      provide: WorkflowService,
      useValue: {
        /**
         * Abre un stream y manda el primer frame en el acto, como el server: la
         * conexión abre y el estado entero viene sin que nadie lo pida.
         */
        stream: (scope: WorkflowScope) => {
          const stream = new WorkflowStreamDouble(scope);
          calls.streams.push(stream);
          return new Observable<WorkflowStreamEvent>((subscriber) => {
            const inner = stream.observable().subscribe(subscriber);
            stream.emit({ kind: 'state', state: calls.answer });
            return () => inner.unsubscribe();
          });
        },
        /**
         * Abre un stream de log y manda la cola del archivo en el acto, como el
         * server: el panel abre "Ver logs" y ya tiene qué mostrar.
         */
        streamLogs: (pid: number, section = 'workflow') => {
          const stream = new WorkflowLogStreamDouble(pid, section);
          calls.logStreams.push(stream);
          return new Observable<LogStreamEvent>((subscriber) => {
            const inner = stream.observable().subscribe(subscriber);
            stream.emit({
              pid,
              text: calls.logText,
              offset: calls.logText.length,
              alive: true,
              gone: false,
            });
            return () => inner.unsubscribe();
          });
        },
        startResource: (key: string, env?: string) => {
          calls.startedKeys.push(key);
          calls.startEnvs.push(env);
          return calls.failStart ? Promise.reject(calls.failStart(new Error('rechazado'))) : Promise.resolve({ ok: true });
        },
        startPort: (port: number, label: string, _env: string, cluster: string) => {
          calls.startedPorts.push({ port, label, cluster });
          return calls.failStart ? Promise.reject(calls.failStart(new Error('rechazado'))) : Promise.resolve({ ok: true });
        },
        stop: (pid: number, allowForeign = false) => {
          calls.stopped.push({ pid, allowForeign });
          return Promise.resolve({ ok: true, stopped: [pid] });
        },
        stopAll: (allowForeign = false) => {
          calls.stopAllCalls.push(allowForeign);
          return Promise.resolve({ ok: true, stopped: [1], refused: [] });
        },
        cleanKafkaStorage: () => {
          calls.kafkaCleanCalls += 1;
          return calls.kafkaCleanError
            ? Promise.reject(calls.kafkaCleanError)
            : Promise.resolve({ ok: true, pid: 0, port: 0, state: 'down', label: 'Kafka storage' });
        },
        addPort: (port: number, _label: string, cluster: string) => {
          calls.addedPorts.push({ port, cluster });
          return Promise.resolve({ ok: true, port });
        },
        renamePort: (cluster: string, port: number, label: string) => {
          calls.renames.push({ cluster, port, label });
          return Promise.resolve({ cluster, ports: [] });
        },
        removePort: (cluster: string, port: number) => {
          calls.removed.push({ cluster, port });
          return Promise.resolve({ cluster, ports: [] });
        },
      },
    },
    {
      provide: EnvironmentService,
      useValue: {
        list: () => {
          calls.envCalls += 1;
          return Promise.resolve({ environments: calls.envs });
        },
      },
    },
  ];
}
