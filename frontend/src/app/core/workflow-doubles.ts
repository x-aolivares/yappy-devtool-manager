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
 */
import { HttpErrorResponse } from '@angular/common/http';
import type { Provider } from '@angular/core';

import type {
  WorkflowPortInfo, WorkflowResourceInfo, WorkflowStateResponse,
} from '../api-gen/models';
import { EnvironmentService } from './services/environment.service';
import { WorkflowService } from './services/workflow.service';

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
    ...over,
  };
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

/** Todo lo que los fakes anotan, para que el test afirme sobre las llamadas. */
export interface WorkflowCalls {
  /** Lo que el endpoint de estado devuelve en la próxima vuelta. */
  answer: WorkflowStateResponse;
  /**
   * Cada pedido de estado, para afirmar a qué ambiente y cluster se le pidió.
   *
   * `env` y `cluster` son opcionales porque hay secciones que no los mandan:
   * Kafka es full local y su ámbito es vacío.
   */
  stateCalls: Array<{ env?: string; cluster?: string }>;
  startedPorts: Array<{ port: number; label: string; cluster: string }>;
  startedKeys: string[];
  /** El ambiente de cada `startedKeys`, en el mismo orden. `undefined` = no mandó. */
  startEnvs: Array<string | undefined>;
  stopped: Array<{ pid: number; allowForeign: boolean }>;
  stopAllCalls: boolean[];
  addedPorts: Array<{ port: number; cluster: string }>;
  renames: Array<{ cluster: string; port: number; label: string }>;
  removed: Array<{ cluster: string; port: number }>;
  /** Cuando no es `null`, el próximo start tira lo que esta función devuelva. */
  failStart: ((err: Error) => Error) | null;
  /** Lo que `/api/workflow/logs` rechaza. `null` = responde bien. */
  logError: HttpErrorResponse | null;
  logCalls: Array<{ pid: number; offset: number }>;
  /** Lo que el log agrega en cada lectura. */
  logText: string;
  /** Cuántas veces se pidió la lista de ambientes. Una sección sin selector, cero. */
  envCalls: number;
}

/** Un `WorkflowCalls` limpio. Cada spec tiene el suyo en una variable propia. */
export function callsVacios(): WorkflowCalls {
  return {
    answer: estado(),
    stateCalls: [],
    startedPorts: [],
    startedKeys: [],
    startEnvs: [],
    stopped: [],
    stopAllCalls: [],
    addedPorts: [],
    renames: [],
    removed: [],
    failStart: null,
    logError: null,
    logCalls: [],
    logText: '',
    envCalls: 0,
  };
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
        state: (env?: string, cluster?: string) => {
          calls.stateCalls.push({ env, cluster });
          return Promise.resolve(calls.answer);
        },
        log: (pid: number, offset: number) => {
          calls.logCalls.push({ pid, offset });
          return calls.logError
            ? Promise.reject(calls.logError)
            : Promise.resolve({
                pid,
                text: calls.logText,
                offset: offset + calls.logText.length,
                alive: true,
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
          return Promise.resolve({ environments: ENVS });
        },
      },
    },
  ];
}
