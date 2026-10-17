/**
 * La base de las cuatro páginas de Workflow.
 *
 * Antes de la separación esto era una sola pantalla con cuatro `<section>`
 * apiladas. Ahora cada sección tiene su ruta y su componente, pero las cuatro leen
 * del **mismo** endpoint (`GET /api/workflow/state`) y comparten casi todo lo que
 * no es el marcado: el poll, los announcements, los modales y las acciones. Copiar
 * eso cuatro veces sería cuatro lugares donde el 404 que corta el poll se puede
 * volver a equivocar.
 *
 * Lo que queda en cada página es lo que de verdad es distinto: **qué filas
 * dibuja**. Eso es `sectionRows`, y es la única cosa que la subclase tiene que
 * decir. Todo lo demás —qué cuenta el resumen, qué se anuncia, qué se ofrece para
 * ver logs— sale de ahí, y por eso las cuatro páginas no pueden discrepar.
 *
 * Un `get` abstracto y no un campo: los inicializadores de campo de la subclase
 * corren **después** de los de la base, así que un `computed()` de la base que
 * leyera un campo de la subclase arrancaría con `undefined`. Un getter vive en el
 * prototipo y ya está resuelto cuando la base lo lee por primera vez.
 */
import {
  DestroyRef, computed, effect, inject, resource, signal,
} from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';

import {
  WorkflowPortInfo, WorkflowResourceInfo, WorkflowStateResponse, WorkflowUnattributedInfo,
} from '../api-gen/models';
import { EnvironmentService } from './services/environment.service';
import { WorkflowService } from './services/workflow.service';
import { toApiError } from './services/api-error';
import {
  canOpen, canShowLogs, canStart, canStop, resourceMeta, startLabel,
  stateAnnouncement, stateBadge, stateBadgeClass,
} from './resources';

/**
 * Cuánto espera entre polls.
 *
 * No es un adorno: el readiness check corre cada medio segundo en el supervisor,
 * y 1.5s son suficientes para que la fila se mueva sin que el developer la esté
 * mirando fija. Con la pestaña oculta el `document.visibilityState` lo sube a
 * 10s — nadie está mirando, y el tracker lo sigue escribiendo igual.
 *
 * Cada página tiene el suyo: se separaron para que se puedan abrir y mirar de a
 * una, no para que se prendan cuatro polls del mismo endpoint a la vez.
 */
const POLL_MS = 1500;
const POLL_HIDDEN_MS = 10000;

/** Una fila de cualquiera de las dos tablas. Lo que las dos tienen en común. */
export type WorkflowRow = WorkflowResourceInfo | WorkflowPortInfo;

/**
 * Con qué ambiente y cluster pregunta la página el estado.
 *
 * Los dos son opcionales a propósito: una sección *full local* —Kafka— no tiene
 * ni ambiente ni cluster que elegir, así que no manda lo que nadie va a cambiar y
 * deja que el backend aplique sus defaults. Lo que la página no elige, no lo pide.
 */
export interface WorkflowScope {
  readonly env?: string;
  readonly cluster?: string;
}

/** El aviso que la página muestra en el `notice-modal`. */
export interface WorkflowNotice {
  readonly title: string;
  readonly paragraphs: readonly string[];
  readonly tone: 'aviso' | 'default' | 'danger';
  /** El rótulo del botón de confirmar. Sin él, el aviso sólo informa. */
  readonly confirmLabel?: string;
  /** Qué corre al confirmar. Sin esto, el aviso no pide nada. */
  readonly onConfirm?: () => void;
}

/** El `pid` de una fila, sea del catálogo o un recurso. */
function rowPid(row: WorkflowRow): number | null {
  return row.pid ?? null;
}

/**
 * Cómo se identifica una fila entre polls.
 *
 * El puerto y la `key` del recurso viven en campos distintos, así que la
 * identidad se arma con lo que cada una tiene. El prefijo `p` evita que un puerto
 * 8402 choque con una key que se llamara `"8402"`.
 */
function rowId(row: WorkflowRow): string {
  return 'port' in row ? `p${row.port}` : `r${row.key}`;
}

/** Cómo se llama una fila en el `aria-live`. Sin nombre, el puerto. */
function rowName(row: WorkflowRow): string {
  if ('port' in row) return row.label || `:${row.port}`;
  return row.name;
}

/**
 * Lo que las cuatro páginas tienen igual.
 *
 * No es un `providedIn: 'root'`: cada página tiene su propia instancia con su
 * propio poll y sus propias señales, y desaparecer con la ruta se lleva el poll
 * con ella. Compartir el estado entre las cuatro fue una opción —un solo poll, y
 * el ambiente y el cluster recordados al navegar— pero el planteo es que cada
 * sección se pueda abrir y mirar sola.
 */
export abstract class WorkflowSectionPage {
  protected readonly workflow = inject(WorkflowService);
  private readonly environmentService = inject(EnvironmentService);
  protected readonly destroyRef = inject(DestroyRef);

  /** Los clusters los elige la página de túneles; el resto los ignora. */
  readonly cluster = signal<string>('cap');

  readonly env = signal('dev');
  readonly envList = signal<{ env: string }[]>([]);

  /**
   * Con qué ambiente y cluster pide esta página el estado.
   *
   * Es un `get` y no un campo por la misma razón que `sectionRows`: los
   * inicializadores de campo de la subclase corren **después** de los de la base,
   * así que un `protected readonly` sobreescrito llegaría `undefined` cuando el
   * constructor de la base lo lee —y la página pediría el estado sin su ámbito en
   * vez del suyo.
   *
   * Kafka lo deja vacío: el broker y Kafdrop corren en el puerto local y no se
   * levantan contra un ambiente ni un cluster. El endpoint los tiene por default,
   * así que la respuesta es la misma; sólo deja de mandar dos query params que
   * nadie iba a cambiar.
   */
  protected get scope(): WorkflowScope {
    return { env: this.env(), cluster: this.cluster() };
  }

  readonly busy = signal(false);
  readonly notice = signal<WorkflowNotice | null>(null);
  /** El `aria-live`. Es la parte accesible más importante de la pantalla. */
  readonly announcement = signal('');

  /**
   * El error del poll, o `''` cuando la última vuelta trajo datos.
   * Estado propio y no `state.error()` del `resource()`: ver `settleFailure`.
   */
  readonly readError = signal('');

  /** Las filas que esta página dibuja, y por lo tanto las que cuenta y anuncia. */
  protected abstract get sectionRows(): readonly WorkflowRow[];

  /** Las mismas filas, para el panel de logs. Se pasa a `<app-workflow-logs>`. */
  readonly logRows = computed<readonly WorkflowRow[]>(() => this.sectionRows);

  /**
   * La foto del estado. Un poll, una request.
   *
   * `request` se recalcula cuando cambia `env` o `cluster`, así que cambiar de
   * cluster abandona la request en vuelo sola: es lo que evita que la respuesta
   * de `cap` llegue después de que el developer ya pasó a `cap2` y le pise la
   * tabla. En una página sin ámbito —Kafka— los params son un objeto vacío y el
   * request sale sin query string.
   */
  readonly state = resource<WorkflowStateResponse, WorkflowScope>({
    params: (): WorkflowScope => this.scope,
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

  // --- el resumen -----------------------------------------------------------

  /** Los contadores de la barra de resumen. Cuentan **esta** página, no el tracker. */
  readonly summary = computed(() => {
    const rows = this.sectionRows;
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

  /** Cuántos hay vivos en la página. Si no hay ninguno, "Parar todo" no tiene sentido. */
  readonly anythingAlive = computed(
    () => this.sectionRows.some((r) => r.state === 'up' || r.state === 'starting'),
  );

  /**
   * Cuántos procesos ajenos alcanza "Parar todo", y son **todos**, no los de la
   * página.
   *
   * `/api/workflow/stop-all` recorre el tracker entero: no le importa qué sección
   * esté abierta ni qué ambiente se esté mirando. Así que el aviso tiene que
   * contar sobre el tracker —darle el número de la página sería subestimar lo que
   * el botón va a matar— mientras que el contador de la barra de resumen sí
   * habla de lo que se ve. Son dos números distintos y por eso son dos signals.
   */
  readonly stopAllForeignCount = computed(
    () =>
      [...this.ports(), ...this.resources()].filter(
        (r) => r.foreign && (r.state === 'up' || r.state === 'starting'),
      ).length,
  );

  // --- el poll --------------------------------------------------------------

  constructor() {
    // La lista de ambientes sólo la necesita la página que puede cambiar de
    // ambiente. Kafka es full local: pedir `/api/environments` para no ofrecer un
    // selector sería un request por apertura que nadie puede usar.
    if (this.scope.env !== undefined) {
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
    }

    this.pollTimer();
    this.watchStates();
  }

  /**
   * El poll.
   *
   * Un `setTimeout` encadenado en vez de `setInterval`: cada tick espera a que el
   * anterior termine, así dos polls lentos no se acumulan. Con `visibilityState`
   * sube el intervalo cuando nadie está mirando — el supervisor sigue escribiendo
   * el tracker igual, sólo se lee menos.
   */
  private pollTimer(): void {
    const tick = () => {
      // `reload()` devuelve un booleano y no una promesa: el fallo no se puede
      // capturar en el `catch`. El `resource()` lo expone por `error()` después
      // de la vuelta, así que se lee en el microtask siguiente — cuando la
      // promise ya se resolvió o rechazó.
      this.state.reload();
      queueMicrotask(() => this.settleFailure(this.state.error()));

      const hidden = typeof document !== 'undefined' && document.visibilityState === 'hidden';
      const timer = setTimeout(tick, hidden ? POLL_HIDDEN_MS : POLL_MS);
      this.destroyRef.onDestroy(() => clearTimeout(timer));
    };
    const first = setTimeout(tick, POLL_MS);
    this.destroyRef.onDestroy(() => clearTimeout(first));
  }

  /**
   * El error que se ve arriba de la pantalla.
   *
   * Es un estado propio y no `state.error()` del `resource()`, por una razón que
   * sólo aparece cuando se apaga el server: el poll de 1,5s sigue corriendo contra
   * un backend muerto, así que cada tick falla, y el último fallo queda clavado
   * para siempre aunque el backend vuelva y el poll siga trayendo datos. La caja
   * roja sobrevive a la recuperación y dice que no se puede leer algo que hace un
   * rato se está leyendo.
   *
   * `settleFailure` recibe el error de la vuelta en vez de leerlo del signal: el
   * `resource()` lo expone como `error()` pero no deja escribirlo, y un test no
   * puede fabricarlo sin andarle los signals por debajo.
   */
  private settleFailure(failed: unknown): void {
    this.readError.set(failed ? describePollFailure(failed) : '');
  }

  /**
   * Anuncia los cambios de estado, una vez por cambio.
   *
   * Se comparan los estados con el poll anterior y se anuncia sólo lo que se
   * movió. Sin esto el lector de pantalla escucharía las mismas filas en cada
   * tick, que es peor que no anunciar nada.
   *
   * Compara `sectionRows` y no el estado entero: el `aria-live` tiene que decir
   * lo que pasó **en esta pantalla**. Anunciar un túnel que se levantó en la
   * página de Kafka mientras se está mirando Túneles sería ruido.
   */
  private watchStates(): void {
    let previous = new Map<string, string>();
    effect(() => {
      const rows = this.sectionRows;
      const changed = rows.filter(
        (row) => previous.get(rowId(row)) !== undefined && previous.get(rowId(row)) !== row.state,
      );
      previous = new Map(rows.map((r) => [rowId(r), r.state]));
      if (!changed.length) return;
      // El último cambio gana el mensaje: cuatro túneles que suben juntos
      // producirían cuatro anuncios encimados.
      this.announcement.set(
        changed.map((row) => stateAnnouncement(rowName(row), row.state)).join('. '),
      );
    });
  }

  // --- acciones -------------------------------------------------------------

  async startResource(row: WorkflowResourceInfo): Promise<void> {
    this.busy.set(true);
    try {
      // El ámbito, no `env()` a secas: una página full local levanta sin
      // ambiente, igual que lee sin ambiente.
      await this.workflow.startResource(row.key, this.scope.env);
      // El `reload` del poll traería el estado solo, pero forzarlo acá hace que
      // la fila cambie en el mismo clic en vez de hasta 1.5s después.
      this.state.reload();
    } catch (err) {
      this.showError('No se pudo levantar', toApiError(err).message);
    } finally {
      this.busy.set(false);
    }
  }

  /** El mismo botón para los dos tipos de fila; el que el catálogo manda tiene puerto. */
  async start(row: WorkflowRow): Promise<void> {
    if ('port' in row) {
      await this.startPort(row);
      return;
    }
    await this.startResource(row);
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

  async stopOne(row: WorkflowRow): Promise<void> {
    const pid = rowPid(row);
    if (!pid) return;
    this.busy.set(true);
    try {
      await this.workflow.stop(pid);
      this.state.reload();
    } catch (err) {
      this.showError('No se pudo parar', toApiError(err).message);
    } finally {
      this.busy.set(false);
    }
  }

  /**
   * "Parar todo" pide confirmación y el aviso dice a quién alcanza.
   *
   * El texto ya no puede decir "de esta pantalla": la acción es global y el
   * contador de ajenos viene del tracker entero —ver `stopAllForeignCount`.
   */
  askStopAll(): void {
    const foreign = this.stopAllForeignCount();
    const paragraphs = [
      'Se van a cerrar todos los túneles y servicios locales que el tracker conoce, en todos los ambientes y clusters — no sólo los de esta pantalla.',
    ];
    if (foreign) {
      paragraphs.push(
        `${foreign} proceso${foreign === 1 ? '' : 's'} ajeno${foreign === 1 ? '' : 's'}. No lo levantó la web, pero el comando los mata igual.`,
      );
    }
    paragraphs.push('Los procesos quedan sin trackear y hay que volver a levantarlos a mano.');
    this.notice.set({
      title: 'Parar todo',
      paragraphs,
      tone: 'danger',
      confirmLabel: 'Parar todo',
      onConfirm: () => void this.confirmStopAll(),
    });
  }

  async confirmStopAll(): Promise<void> {
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

  // --- avisos ---------------------------------------------------------------

  /** El aviso de error: informa y se cierra. No confirma nada. */
  protected showError(title: string, detail: string): void {
    this.notice.set({ title, paragraphs: [detail], tone: 'aviso' });
  }

  /** El botón de "aceptar" del aviso. Sólo hace algo si el aviso lo pedía. */
  confirmNotice(): void {
    this.notice()?.onConfirm?.();
  }

  dismissNotice(): void { this.notice.set(null); }

  // --- cómo se lee una fila -------------------------------------------------
  // Referencia directa a las funciones puras de `core/resources`, no wrappers:
  // la regla de "un botón que siempre va a fallar es peor que ninguno" tiene que
  // estar escrita una sola vez en el repo.

  protected readonly meta = resourceMeta;
  protected readonly badge = stateBadge;
  protected readonly badgeClass = stateBadgeClass;
  protected readonly startText = startLabel;
  protected readonly canStartRow = canStart;
  protected readonly canOpenRow = canOpen;
  protected readonly canShowLogsRow = canShowLogs;
  protected readonly canStopRow = canStop;

  /** `canStopRow` con dos argumentos; en el template se lee mejor así. */
  protected canStop(row: WorkflowRow): boolean {
    return canStop(row.state, !!row.foreign);
  }

  // --- herramientas para los tests ------------------------------------------

  /**
   * Expuesto para tests: la caja desaparece cuando la vuelta trae datos.
   *
   * No se prueba por el `reload()` del `resource()` porque en un test el recurso
   * resuelve de inmediato y no hay tick que capturar. Lo que importa es la regla:
   * un error sin recuperación se muestra, y con recuperación se va.
   */
  settleFailureForTest(failed: unknown): void { this.settleFailure(failed); }

  describePollFailureForTest(err: unknown): string { return describePollFailure(err); }
}

/**
 * El texto de un error del poll.
 *
 * El `error-box` va arriba de todo y es lo único que hay entre una caída del
 * backend y una pantalla que sigue mostrando la última foto buena. Dice qué
 * pasó, no sólo que pasó algo.
 */
export function describePollFailure(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    const body = err.error as { detail?: string } | null;
    if (body?.detail) return body.detail;
    if (err.status === 0) return 'No se pudo conectar con el servidor.';
    return `El servidor respondió ${err.status}.`;
  }
  return toApiError(err).message;
}

export const EMPTY_STATE: WorkflowStateResponse = {
  cluster: '', env: '', generated_at: 0, catalog_configured: false,
  ports: [], remote_host: '', resources: [], unattributed: [],
};
