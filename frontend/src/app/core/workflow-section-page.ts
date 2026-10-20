/**
 * La base de las cuatro páginas de Workflow.
 *
 * Antes de la separación esto era una sola pantalla con cuatro `<section>`
 * apiladas. Ahora cada sección tiene su ruta y su componente, pero las cuatro
 * escuchan el **mismo** stream (`GET /api/workflow/stream`) y comparten casi todo
 * lo que no es el marcado: los announcements, los modales y las acciones. Copiar
 * eso cuatro veces sería cuatro lugares donde se puede volver a equivocar uno.
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
  DestroyRef, computed, effect, inject, signal,
} from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import type { Subscription } from 'rxjs';

import {
  WorkflowPortInfo, WorkflowResourceInfo, WorkflowStateResponse, WorkflowUnattributedInfo,
} from '../api-gen/models';
import { EnvironmentService } from './services/environment.service';
import {
  WorkflowScope, WorkflowService, WorkflowStreamEvent,
} from './services/workflow.service';
import { toApiError } from './services/api-error';
import { awsEnvironments } from './format';
import {
  canOpen, canShowLogs, canStart, canStop, resourceMeta, startLabel,
  stateAnnouncement, stateBadge, stateBadgeClass,
} from './resources';

export type { WorkflowScope } from './services/workflow.service';

/** Una fila de cualquiera de las dos tablas. Lo que las dos tienen en común. */
export type WorkflowRow = WorkflowResourceInfo | WorkflowPortInfo;

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
   * Si esta página ya sabe con qué ambiente habla.
   *
   * Las páginas con selector lo aprenden de la lista de ambientes, y la lista es
   * una request: hasta que llega, `env` es una **sospecha** —"dev"—, no un
   * ambiente. Mandarla antes de tiempo es lo que producía un
   * `GET /api/workflow/stream?env=dev` que el backend contestaba con `400` en
   * cualquier máquina donde ese ambiente no existe: un 400 que la pantalla no
   * sabe explicar y que el error-box lee como "no se pudo conectar con el
   * servidor".
   *
   * Por eso el stream no se abre hasta que la lista llegó. Kafka no tiene
   * lista —es full local y su `scope` no tiene ambiente—, así que para esa
   * página esto es `true` desde el principio y el stream abre en el acto.
   */
  private readonly envKnown = signal(this.scope.env === undefined);

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
   * El error de la conexión, o `''` cuando el último frame trajo datos.
   *
   * Se va solo con el próximo frame, no con un `reload()`: es la misma regla de
   * antes —una caja de error que sobrevive a la recuperación dice que no se puede
   * leer algo que hace un rato se está leyendo— y ahora el frame que la borra es
   * el mismo que la tablero ya tenía.
   */
  readonly readError = signal('');

  /** Las filas que esta página dibuja, y por lo tanto las que cuenta y anuncia. */
  protected abstract get sectionRows(): readonly WorkflowRow[];

  /**
   * El nombre corto de la sección: `kafka`, `tunnels`, `services`, `database`.
   *
   * Viaja en la URL de cada pedido que la pantalla hace, porque el access log del
   * server imprime el path y nada más: sin esto, cuatro secciones golpeando
   * `/api/workflow/logs` dan líneas indistinguibles y no hay forma de saber cuál
   * de ellas estaba preguntando.
   *
   * Abstracto y no un default en la base a propósito: una página que no lo declara
   * no compila, que es el recordatorio de que si aparece una quinta hay que darle
   * nombre.
   */
  abstract readonly section: string;

  /** Las mismas filas, para el panel de logs. Se pasa a `<app-workflow-logs>`. */
  readonly logRows = computed<readonly WorkflowRow[]>(() => this.sectionRows);

  /**
   * La foto del estado, empujada.
   *
   * Un `signal` y no un `resource()`: no hay nada que resolver, sólo algo que
   * escuchar. `listen()` abre la conexión y la vuelve a abrir si cambia el ámbito.
   */
  protected readonly state = signal<WorkflowStateResponse>(EMPTY_STATE);

  readonly remoteHost = computed<string>(() => this.state().remote_host);
  /**
   * El ambiente elegido se alcanza por TCP: no tiene SSM, ECS ni Aurora.
   *
   * El backend ya no manda esas filas; esto es para que el vacío diga *por qué*
   * está vacío en vez de ofrecer un botón que sólo puede fallar.
   */
  readonly isLocal = computed<boolean>(() => this.state().is_local ?? false);
  /** False cuando `CLUSTER_PORTS` no está seteado: el vacío se arregla distinto. */
  readonly catalogConfigured = computed<boolean>(
    () => this.state().catalog_configured ?? false,
  );
  readonly resources = computed<WorkflowResourceInfo[]>(() => this.state().resources);
  readonly ports = computed<WorkflowPortInfo[]>(() => this.state().ports);
  readonly unattributed = computed<WorkflowUnattributedInfo[]>(
    () => this.state().unattributed ?? [],
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
          // **Sin los locales.** `local` es un ambiente de base de datos: se
          // alcanza por TCP contra el MySQL de la máquina, y no tiene SSM ni
          // Parameter Store. Todo lo que esta sección abre —los túneles del
          // catálogo, la fila de Aurora, Databricks— es de AWS, así que
          // ofrecerlo acá es ofrecer una opción que la pantalla misma dice que
          // no sirve. Es el mismo filtro que usan las páginas de parámetros.
          const list = (awsEnvironments(res.environments) ?? []).map(
            (e: { env: string }) => ({ env: e.env }),
          );
          this.envList.set(list);
          if (!list.length) {
            // No hay ambientes: no hay contra qué hablar. Decirlo es mejor que
            // abrir un stream preguntando por uno que no existe.
            this.readError.set('No hay ambientes configurados.');
            return;
          }
          // El ambiente elegido tiene que existir: si la máquina no lo tiene, el
          // primero de la lista es el que sí.
          if (!list.some((e: { env: string }) => e.env === this.env())) {
            this.env.set(list[0].env);
          }
          this.envKnown.set(true);
        })
        .catch(() => {
          // El error lo muestra el `error-box`; acá sólo se deja la lista vacía
          // y el stream sin abrir: sin la lista no se sabe con qué ambiente
          // hablar, y adivinar es lo que producía el 400.
          this.envList.set([]);
          this.readError.set('No se pudo leer la lista de ambientes.');
        });
    }

    this.listen();
    this.watchStates();
  }

  /**
   * Abre el stream, y lo vuelve a abrir si cambia el ámbito.
   *
   * Un `effect` y no una suscripción sola porque `scope` se puede cambiar: la página
   * de túneles tiene un selector de cluster. Leerlo acá adentro es lo que registra
   * la dependencia, así que cambiar de cluster cierra el stream anterior y abre
   * otro — sin esto, el frame de `cap` podría llegar después de que el developer ya
   * pasó a `cap2` y pisarle la tabla.
   *
   * `onCleanup` corre **antes** de la siguiente vuelta, y el `unsubscribe` es lo
   * que cierra el `EventSource` del otro lado: abandonar la ruta no puede dejar una
   * conexión abierta.
   */
  private listen(): void {
    effect((onCleanup) => {
      // Sin ambiente conocido no hay stream. Preguntar por un ambiente que
      // puede no existir es un 400 que la pantalla no sabe explicar.
      if (!this.envKnown()) return;
      const scope = this.scope;
      const subscription: Subscription = this.workflow
        .stream(scope)
        .subscribe((event) => this.apply(event));
      onCleanup(() => subscription.unsubscribe());
    });
  }

  /**
   * Lo que llega del stream, aplicado a la pantalla.
   *
   * Un frame borra el error anterior: la conexión se cayó, vuelve, y manda el
   * estado entero. Es la misma regla que tenía el poll —la caja roja no sobrevive
   * a la recuperación— pero ahora la resuelve el mismo frame que repinta la tabla.
   */
  private apply(event: WorkflowStreamEvent): void {
    if (event.kind === 'error') {
      this.readError.set(event.detail);
      return;
    }
    this.readError.set('');
    this.state.set(event.state);
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
      // Sin `reload()`. El backend ya escribió el tracker y ya publicó antes de
      // contestar, así que el frame está viniendo: la fila cambia en el mismo clic
      // y preguntar otra vez sería justamente la consulta de más.
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
    } catch (err) {
      this.showError('No se pudo parar', toApiError(err).message);
    } finally {
      this.busy.set(false);
    }
  }

  /**
   * The remedy the backend proposed, and what it costs.
   *
   * Asks before doing it, and says exactly what is lost. Cleaning the Kafka storage
   * deletes every local topic and its messages — that is the price of the fix, and
   * a button that does it without saying so is a button that eats somebody's data
   * by surprise.
   *
   * The wording lives here and not in the card because the card does not know what
   * the action destroys; `FIXES` in `resource-cards` knows the name, this knows
   * the consequence.
   */
  askFix(row: WorkflowResourceInfo): void {
    const paragraphs = [
      'Se borra el storage local de Kafka: todos los topics del broker, con sus mensajes y su configuración.',
      'Después queda formateado y vacío, con un id de cluster nuevo. Es la única forma de que levante cuando quedó bloqueado un archivo de su metadata.',
      'Los servicios que hoy no están corriendo no se ven afectados.',
    ];
    this.notice.set({
      title: 'Limpiar el storage de Kafka',
      paragraphs,
      tone: 'danger',
      confirmLabel: 'Limpiar y formatear',
      onConfirm: () => void this.runFix(row),
    });
  }

  async runFix(row: WorkflowResourceInfo): Promise<void> {
    this.notice.set(null);
    this.busy.set(true);
    try {
      await this.workflow.cleanKafkaStorage();
    } catch (err) {
      this.showError('No se pudo limpiar', toApiError(err).message);
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
}

export const EMPTY_STATE: WorkflowStateResponse = {
  cluster: '', env: '', generated_at: 0, is_local: false, catalog_configured: false,
  ports: [], remote_host: '', resources: [], unattributed: [],
};
