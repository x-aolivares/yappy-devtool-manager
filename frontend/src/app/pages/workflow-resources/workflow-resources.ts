import {
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  resource,
  signal,
} from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';

import {
  WorkflowPortInfo,
  WorkflowResourceInfo,
  WorkflowStateResponse,
  WorkflowUnattributedInfo,
} from '../../api-gen/models';
import { EnvironmentService } from '../../core/services/environment.service';
import { WorkflowService } from '../../core/services/workflow.service';
import { toApiError } from '../../core/services/api-error';
import {
  canOpen,
  canShowLogs,
  canStart,
  canStop,
  resourceMeta,
  startLabel,
  stateAnnouncement,
  stateBadge,
  stateBadgeClass,
  stateLabel,
} from '../../core/resources';
import { BusyModal } from '../../components/busy-modal/busy-modal';
import { NoticeModal } from '../../components/notice-modal/notice-modal';

/** Los clusters que el backend conoce. El host sale del estado, no de acá. */
const CLUSTERS = ['cap', 'cap2', 'int'] as const;

/**
 * Cuánto espera entre polls.
 *
 * No es un adorno: el readiness check corre cada medio segundo en el supervisor,
 * y 1.5s es suficientes para que la fila se mueva sin que el developer la esté
 * mirando fija. Con la pestaña oculta el `document.visibilityState` lo sube a
 * 10s — nadie está mirando, y el tracker lo sigue escribiendo igual.
 */
const POLL_MS = 1500;
const POLL_HIDDEN_MS = 10000;

/** El guard de escritura del nombre del servicio. Ver `writeLabel`. */
const LABEL_DEBOUNCE_MS = 500;

/**
 * Recursos locales y túneles SSM: qué está arriba, qué se puede levantar, y
 * quélogs está escribiendo.
 *
 * Toda la pantalla es una consecuencia de una idea: **el backend no sabe si un
 * túnel funciona hasta que algo lee lo que responde**. Un `ssm start-session`
 * bindea el puerto local enseguida y puede quedarse ahí aceptando conexiones que
 * no mueven un byte. Por eso hay un `starting` que dura, y por eso la fila no se
 * pone verde hasta que el probe leyó una respuesta de verdad.
 *
 * De ahí salen las tres reglas de esta pantalla:
 *
 * - **El estado se lee con `resource()`, no con un `setInterval`.** Es la
 *   primera página del repo que hace polling, así que queda documentado en
 *   `docs/lenguaje-visual.md` para que la segunda no lo vuelva a decidir.
 * - **Los cambios de estado se anuncian en un `aria-live`.** Todo en esta
 *   pantalla cambia asíncronamente y sin mover el foco: para quien no ve el
 *   punto de color, no hay ningún otro aviso de que el túnel quedó arriba.
 * - **Una fila que no es de la web no se para desde la fila.** `kill_ssm()`
 *   trabaja sobre el tracker y no le importa quién levantó qué, así que el botón
 *   de una fila nunca alcanza un proceso con borde punteado. Sólo "Parar todo" lo
 *   hace, y avisa antes.
 */
@Component({
  selector: 'app-workflow-resources-page',
  imports: [BusyModal, NoticeModal],
  templateUrl: './workflow-resources.html',
  styleUrl: './workflow-resources.scss',
})
export class WorkflowResourcesPage {
  private readonly workflow = inject(WorkflowService);
  private readonly environmentService = inject(EnvironmentService);
  private readonly destroyRef = inject(DestroyRef);

  readonly clusters = CLUSTERS;

  readonly env = signal('dev');
  readonly cluster = signal<string>('cap');
  readonly envList = signal<{ env: string }[]>([]);

  readonly addPortValue = signal('');
  readonly addPortError = signal('');
  readonly busy = signal(false);
  readonly notice = signal<{ title: string; paragraphs: string[]; tone: 'aviso' | 'default' | 'danger' } | null>(null);
  readonly stopAllPending = signal(false);
  readonly logPid = signal<number | null>(null);
  readonly logText = signal('');
  /** El `aria-live`. Es la parte accesible más importante de la pantalla. */
  readonly announcement = signal('');

  private readonly labelTimers = new Map<number, ReturnType<typeof setTimeout>>();

  /**
   * La foto del estado. Un poll, una request.
   *
   * `request` se recalcula cuando cambia `env` o `cluster`, así que cambiar de
   * cluster abandona la request en vuelo sola: es lo que evita que la respuesta
   * de `cap` llegue después de que el developer ya pasó a `cap2` y le pise la
   * tabla.
   */
  readonly state = resource<WorkflowStateResponse, { env: string; cluster: string }>({
    params: (): { env: string; cluster: string } => ({
      env: this.env(),
      cluster: this.cluster(),
    }),
    loader: ({ params, abortSignal }) =>
      this.workflow.state(params.env, params.cluster, abortSignal),
    defaultValue: EMPTY_STATE,
  });

  readonly remoteHost = computed<string>(() => this.state.value().remote_host);
  /** False cuando `CLUSTER_PORTS` no está seteado: el vacío se arregla distinto. */
  readonly catalogConfigured = computed<boolean>(
    () => this.state.value().catalog_configured ?? false,
  );
  readonly resources = computed<WorkflowResourceInfo[]>(() => this.state.value().resources);
  readonly ports = computed<WorkflowPortInfo[]>(() => this.state.value().ports);
  readonly unattributed = computed<WorkflowUnattributedInfo[]>(
    () => this.state.value().unattributed ?? [],
  );

  readonly kafka = computed<WorkflowResourceInfo[]>(() =>
    this.resources().filter((r: WorkflowResourceInfo) => r.group === 'kafka'),
  );
  readonly named = computed<WorkflowResourceInfo[]>(() =>
    this.resources().filter((r: WorkflowResourceInfo) => r.group === 'named'),
  );
  readonly db = computed<WorkflowResourceInfo[]>(() =>
    this.resources().filter((r: WorkflowResourceInfo) => r.group === 'db'),
  );

  /** "3 puertos · 2 con nombre". Lo que el contador tiene que poder decir. */
  readonly catalogSummary = computed(() => {
    const rows = this.ports();
    const named = rows.filter((p) => p.label.trim()).length;
    return `${rows.length} ${rows.length === 1 ? 'puerto' : 'puertos'} · ${named} con nombre`;
  });

  /** Los contadores de la barra de resumen. */
  readonly summary = computed(() => {
    const rows = [...this.ports(), ...this.resources()];
    const up = rows.filter((r) => r.state === 'up').length;
    const starting = rows.filter((r) => r.state === 'starting').length;
    const problems = rows.filter((r) => r.state === 'error' || r.state === 'dead').length;
    const foreign = rows.filter((r) => r.foreign && r.state !== 'down').length;
    const parts: string[] = [`${up} arriba`];
    if (starting) parts.push(`${starting} levantando`);
    if (problems) parts.push(`${problems} con problemas`);
    if (foreign) parts.push(`${foreign} levantado fuera de la web`);
    return parts.join(' · ');
  });

  /** Cuántos hay vivos. Si no hay ninguno, "Parar todo" no tiene sentido. */
  readonly anythingAlive = computed(
    () => [...this.ports(), ...this.resources()].some((r) => r.state === 'up' || r.state === 'starting'),
  );

  /** El texto de "Parar todo" dice a quién alcanza, no solo qué hace. */
  readonly foreignCount = computed(
    () =>
      [...this.ports(), ...this.resources()].filter(
        (r) => r.foreign && (r.state === 'up' || r.state === 'starting'),
      ).length,
  );

  /** El detalle de una tarjeta, ya formateado. */
  meta(row: WorkflowResourceInfo | WorkflowPortInfo): string {
    return resourceMeta(row);
  }

  /** El texto de la celda de estado, con el "hace X" cuando lo hay. */
  cellState(state: string, since: number | null): string {
    return stateLabel(state, since);
  }

  /** El texto del badge corto. Sin timestamp: un badge con dos datos no cabe. */
  badge(state: string): string {
    return stateBadge(state);
  }

  badgeClass(state: string): string {
    return stateBadgeClass(state);
  }

  startText(state: string): string {
    return startLabel(state);
  }

  canStartRow(state: string): boolean {
    return canStart(state);
  }

  canOpenRow(state: string): boolean {
    return canOpen(state);
  }

  canShowLogsRow(state: string): boolean {
    return canShowLogs(state);
  }

  canStopRow(state: string, foreign?: boolean): boolean {
    return canStop(state, !!foreign);
  }

  constructor() {
    this.environmentService
      .list()
      .then((res) => {
        const list = res.environments.map((e: { env: string }) => ({ env: e.env }));
        this.envList.set(list);
        if (list.length && !list.some((e: { env: string }) => e.env === this.env())) {
          this.env.set(list[0].env);
        }
      })
      .catch(() => {
        // El error lo muestra el `error-box` de `resource()` o el aviso; acá sólo
        // se deja la lista vacía y el `<select>` con el valor actual.
        this.envList.set([]);
      });

    this.pollTimer();
    this.watchStates();

    this.destroyRef.onDestroy(() => {
      for (const timer of this.labelTimers.values()) clearTimeout(timer);
      this.labelTimers.clear();
    });
  }

  /**
   * El poll.
   *
   * Un `setInterval` con `setTimeout` encadenado en vez de `setInterval`: cada
   * tick espera a que el anterior termine, así dos polls lentos no se acumulan.
   * Con `visibilityState` sube el intervalo cuando nadie está mirando — el
   * supervisor sigue escribiendo el tracker igual, sólo se lee menos.
   */
  private pollTimer(): void {
    const tick = () => {
      this.state.reload();
      this.pollLog();
      const hidden = typeof document !== 'undefined' && document.visibilityState === 'hidden';
      const timer = setTimeout(tick, hidden ? POLL_HIDDEN_MS : POLL_MS);
      this.destroyRef.onDestroy(() => clearTimeout(timer));
    };
    const first = setTimeout(tick, POLL_MS);
    this.destroyRef.onDestroy(() => clearTimeout(first));
  }

  /**
   * Anuncia los cambios de estado, una vez por cambio.
   *
   * Se comparan los estados con el poll anterior y se anuncia sólo lo que se
   * movió. Sin esto el lector de pantalla escucharía las mismas filas en cada
   * tick, que es peor que no anunciar nada.
   */
  private watchStates(): void {
    let previous = new Map<string, string>();
    effect(() => {
      const rows: Array<{ id: string; name: string; state: string }> = [
        ...this.ports().map((p: WorkflowPortInfo) => ({
          id: `p${p.port}`,
          name: p.label || `:${p.port}`,
          state: p.state,
        })),
        ...this.resources().map((r: WorkflowResourceInfo) => ({
          id: r.key,
          name: r.name,
          state: r.state,
        })),
      ];
      const changed = rows.filter((row) => previous.get(row.id) !== undefined && previous.get(row.id) !== row.state);
      previous = new Map(rows.map((r) => [r.id, r.state]));
      if (!changed.length) return;
      // El último cambio gana el mensaje: cuatro túneles que suben juntos
      // producirían cuatro anuncios encimados.
      this.announcement.set(
        changed.map((row) => stateAnnouncement(row.name, row.state)).join('. '),
      );
    });
  }

  // --- acciones -------------------------------------------------------------

  async startResource(row: WorkflowResourceInfo): Promise<void> {
    this.busy.set(true);
    try {
      await this.workflow.startResource(row.key, this.env());
      // El `reload` del poll traería el estado solo, pero forzarlo acá hace que
      // la fila cambie en el mismo clic en vez de hasta 1.5s después.
      this.state.reload();
    } catch (err) {
      this.showError('No se pudo levantar', toApiError(err).message);
    } finally {
      this.busy.set(false);
    }
  }

  async startPort(row: WorkflowPortInfo): Promise<void> {
    this.busy.set(true);
    try {
      await this.workflow.startPort(row.port, row.label, this.env(), this.cluster());
      this.state.reload();
    } catch (err) {
      this.showError('No se pudo levantar el túnel', toApiError(err).message);
    } finally {
      this.busy.set(false);
    }
  }

  async stopOne(row: WorkflowResourceInfo | WorkflowPortInfo): Promise<void> {
    if (!row.pid) return;
    this.busy.set(true);
    try {
      await this.workflow.stop(row.pid);
      this.state.reload();
    } catch (err) {
      this.showError('No se pudo parar', toApiError(err).message);
    } finally {
      this.busy.set(false);
    }
  }

  /** "Parar todo" pide confirmación y el aviso dice a quién alcanza. */
  askStopAll(): void {
    const foreign = this.foreignCount();
    const paragraphs = [
      'Se van a cerrar los túneles y los servicios locales de esta pantalla.',
    ];
    if (foreign) {
      paragraphs.push(
        `${foreign} proceso${foreign === 1 ? '' : 's'} ajeno${foreign === 1 ? '' : 's'}. No lo levantó la web, pero el comando los mata igual.`,
      );
    }
    paragraphs.push('Los procesos quedan sin trackear y hay que volver a levantarlos a mano.');
    this.stopAllPending.set(true);
    this.notice.set({ title: 'Parar todo', paragraphs, tone: 'danger' });
  }

  cancelStopAll(): void {
    this.stopAllPending.set(false);
    this.notice.set(null);
  }

  async confirmStopAll(): Promise<void> {
    if (!this.stopAllPending()) return;
    this.stopAllPending.set(false);
    this.notice.set(null);
    this.busy.set(true);
    try {
      await this.workflow.stopAll(true);
      this.state.reload();
    } catch (err) {
      this.showError('No se pudo parar todo', toApiError(err).message);
    } finally {
      this.busy.set(false);
    }
  }

  // --- catálogo -------------------------------------------------------------

  async addPort(): Promise<void> {
    const raw = this.addPortValue().trim();
    this.addPortError.set('');
    if (!raw) {
      this.addPortError.set('Falta el número de puerto.');
      return;
    }
    const port = Number(raw);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      this.addPortError.set('Tiene que ser un puerto entre 1 y 65535.');
      return;
    }
    try {
      await this.workflow.addPort(port, '', this.cluster());
      this.addPortValue.set('');
      this.state.reload();
    } catch (err) {
      this.addPortError.set(toApiError(err).message);
    }
  }

  onAddPortKey(event: Event): void {
    if ((event as KeyboardEvent).key === 'Enter') {
      event.preventDefault();
      void this.addPort();
    }
  }

  /** Si hay alguna fila del catálogo que se pueda levantar de un click. */
  readonly hasDown = computed<boolean>(() =>
    this.ports().some((p: WorkflowPortInfo) => p.state === 'down'),
  );

  /**
   * Levanta todas las filas que están abajo.
   *
   * Sólo `down`, a diferencia del botón de cada fila, que sí ofrece las caídas.
   * Un botón multiple no tiene dónde decir "esto se cayó": apretarlo con tres
   * filas caídas y verlas volver a caerse no es lo que "Levantar todos" promete.
   * Para ésas está el botón de su fila, que sí lo dice.
   */
  async startAllDown(): Promise<void> {
    const pending = this.ports().filter((p: WorkflowPortInfo) => p.state === 'down');
    if (!pending.length) return;
    this.busy.set(true);
    // Uno por uno y no en paralelo: cada túnel es un `ssm start-session` contra
   // la misma instancia, y lanzarlos de a cinco se pelean por la sesión.
    for (const row of pending) {
      try {
        await this.workflow.startPort(row.port, row.label, this.env(), this.cluster());
      } catch (err) {
        // Uno que falla no cancela los demás: el developer quiere los que puedan
        // y el que no pudo queda con su error de antes.
        this.showError('No se pudo levantar', toApiError(err).message);
        break;
      }
    }
    this.busy.set(false);
    this.state.reload();
  }

  /**
   * El nombre del servicio se guarda solo, con debounce.
   *
   * El mockup guarda en cada tecla porque su catálogo está en `localStorage` y
   * una escritura a memoria no cuesta nada. Acá el catálogo es `CLUSTER_PORTS`
   * en `env.base`: guardar en cada tecla sería escribir un archivo treinta veces
   * por palabra. El debounce no es una diferencia de estilo, es no escribir el
   * archivo de un usuario treinta veces.
   */
  writeLabel(row: WorkflowPortInfo, value: string): void {
    const pending = this.labelTimers.get(row.port);
    if (pending) clearTimeout(pending);
    this.labelTimers.set(
      row.port,
      setTimeout(() => {
        this.labelTimers.delete(row.port);
        if (row.label === value.trim()) return;
        void this.workflow
          .renamePort(this.cluster(), row.port, value.trim())
          .then(() => this.state.reload())
          .catch((err) => this.showError('No se pudo guardar el nombre', toApiError(err).message));
      }, LABEL_DEBOUNCE_MS),
    );
  }

  /**
   * Quitar la fila.
   *
   * Primero para el túnel si está vivo: la fila que se borra es lo único que lo
   * puede parar. El backend igual lo rechaza — es la defensa del lado del
   * servidor, no un chequeo de acá.
   */
  async removePort(row: WorkflowPortInfo): Promise<void> {
    this.busy.set(true);
    try {
      if (row.state === 'up' || row.state === 'starting') {
        if (row.pid) await this.workflow.stop(row.pid);
      }
      await this.workflow.removePort(this.cluster(), row.port);
      this.state.reload();
    } catch (err) {
      this.showError('No se pudo quitar el puerto', toApiError(err).message);
    } finally {
      this.busy.set(false);
    }
  }

  // --- logs -----------------------------------------------------------------

  /**
   * "Ver logs" filtra por ese recurso y abre el panel.
   *
   * El offset arranca en el tamaño del archivo, no en cero: la primera lectura
   * pide el final y desde ahí sólo lo que se agrega. Pedir las últimas 60 líneas
   * en el primer poll y después el offset evita el solapamiento entre lecturas.
   */
  showLogs(row: WorkflowResourceInfo | WorkflowPortInfo): void {
    this.logPid.set(row.pid ?? null);
    this.logText.set('');
    this.logOffset.set(0);
    this.logLoaded.set(false);
    if (row.pid) void this.appendLog(row.pid);
  }

  /** El poll del log, en el mismo tick que el del estado. */
  private pollLog(): void {
    const pid = this.logPid();
    if (pid) void this.appendLog(pid);
  }

  /**
   * El primer proceso con log, para el "Ver logs" de la barra de resumen.
   *
   * La barra no es un recurso: no tiene fila ni botón propio, así que "ver logs"
   * sin más abierta el primer túnel que tenga archivo. El de la tabla gana
   * porque es el que el developer acaba de pedir.
   */
  readonly firstLogPid = computed<number | null>(() => {
    const chosen = this.logPid();
    if (chosen) return chosen;
    const rows = [...this.ports(), ...this.resources()];
    return rows.find((r) => r.log_file && canShowLogs(r.state))?.pid ?? null;
  });

  showFirstLog(): void {
    const pid = this.firstLogPid();
    if (!pid) return;
    const row = [...this.ports(), ...this.resources()].find((r) => r.pid === pid);
    if (row) {
      this.showLogs(row);
      return;
    }
    this.logPid.set(pid);
    this.logText.set('');
    this.logOffset.set(0);
    void this.appendLog(pid);
  }

  closeLogs(): void {
    this.logPid.set(null);
    this.logText.set('');
    this.logOffset.set(0);
    this.logLoaded.set(false);
  }

  readonly logOffset = signal(0);
  /** Distingue "todavía no llegó" de "llegó vacío". */
  readonly logLoaded = signal(false);

  /** El log del recurso elegido. `null` = el panel está cerrado. */
  readonly logScope = computed<number | null>(() => this.logPid());

  private async appendLog(pid: number): Promise<void> {
    try {
      const tail = await this.workflow.log(pid, this.logOffset());
      if (tail.text) this.logText.update((prev) => (prev + tail.text).slice(-20000));
      this.logOffset.set(tail.offset);
      this.logLoaded.set(true);
    } catch {
      // El log desapareció con el proceso. No es un fallo de la pantalla: el
      // panel simplemente deja de crecer.
      this.logLoaded.set(true);
    }
  }

  // --- avisos ---------------------------------------------------------------

  private showError(title: string, detail: string): void {
    this.notice.set({ title, paragraphs: [detail], tone: 'aviso' });
  }

  /**
   * El texto de un error del poll.
   *
   * El `error-box` va arriba de todo y es lo único que hay entre una caída del
   * backend y una pantalla que sigue mostrando la última foto buena. Dice qué
   * pasó, no sólo que pasó algo.
   */
  readError(err: unknown): string {
    if (err instanceof HttpErrorResponse) {
      const body = err.error as { detail?: string } | null;
      if (body?.detail) return body.detail;
      if (err.status === 0) return 'No se pudo conectar con el servidor.';
      return `El servidor respondió ${err.status}.`;
    }
    return toApiError(err).message;
  }

  dismissNotice(): void {
    this.notice.set(null);
  }

  trackRow(_index: number, row: WorkflowResourceInfo): string {
    return row.key;
  }

  trackPort(_index: number, row: WorkflowPortInfo): number {
    return row.port;
  }
}

const EMPTY_STATE: WorkflowStateResponse = {
  cluster: '',
  env: '',
  generated_at: 0,
  catalog_configured: false,
  ports: [],
  remote_host: '',
  resources: [],
  unattributed: [],
};