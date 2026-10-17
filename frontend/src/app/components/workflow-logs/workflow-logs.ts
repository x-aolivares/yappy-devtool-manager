/**
 * El panel de logs, compartido por las cuatro páginas de Workflow.
 *
 * Nació dentro de la pantalla única y hoy lo usan Kafka, Túneles, Servicios y Base
 * de datos. Es el componente que más vale la pena tener suelto: seguir un log es
 * una máquina de estados con tres salidas —sigue, se cortó, se cerró— y cada una
 * tiene un error detrás que ya se cometió una vez.
 *
 * **El panel sigue un pid porque el developer apretó "Ver logs", no porque la
 * pantalla esté mirando.** Con la fila sola —incluso arriba y con archivo— no se
 * abre ninguna conexión. Por eso la conexión vive adentro del componente y arranca
 * con `open()`, no con la fila.
 *
 * El `gone` es la mitad de la regla: el pid se sigue mostrando, pero ya no se le
 * pregunta. Antes era un 404 en el tick siguiente; ahora es un frame que dice que
 * el pid se fue, y la conexión se cierra. Sin ese corte, un proceso que murió
 * dejaba el panel golpeando `/logs` cada 1,5s para siempre — eso es lo que llenaba
 * el log del server de 404s.
 */
import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';

import { LogStreamEvent, WorkflowService } from '../../core/services/workflow.service';
import type { WorkflowRow } from '../../core/workflow-section-page';
import { canShowLogs } from '../../core/resources';

/**
 * Cuánto texto se guarda.
 *
 * Es un tope, no una cola: se corta por arriba. Un log de arranque no pasa de unas
 * pocas centenas de líneas, y lo que se corta es lo de hace un rato, que es lo que
 * el developer ya llegó a leer.
 */
const MAX_CHARS = 20000;

@Component({
  selector: 'app-workflow-logs',
  templateUrl: './workflow-logs.html',
  styleUrl: './workflow-logs.scss',
})
export class WorkflowLogs {
  private readonly workflow = inject(WorkflowService);

  /**
   * Las filas que el "Ver logs" de la barra puede ofrecer.
   *
   * Es un input y no una lista fija porque el atajo es "el primer recurso de esta
   * pantalla que tenga archivo": en Kafka es Kafdrop, en Túneles es el primer
   * puerto con nombre. La página decide cuáles son sus filas y el panel no sabe
   * qué sección es.
   */
  readonly rows = input<readonly WorkflowRow[]>([]);

  /**
   * La sección que está mirando este panel, para que el pedido lo diga.
   *
   * Va en la URL porque el access log del server imprime el path y nada más. Con
   * las cuatro secciones usando el mismo panel, `GET /api/workflow/logs?pid=20192`
   * no dice si la pregunta venía de Kafka o de Túneles — y en medio de un log lleno
   * de líneas iguales, esa es justo la información que falta.
   */
  readonly section = input<string>('workflow');

  /** El pid que se está mirando. `null` = el panel está cerrado. */
  readonly pid = signal<number | null>(null);
  readonly text = signal('');
  readonly offset = signal(0);
  /** Distingue "todavía no llegó" de "llegó vacío". */
  readonly loaded = signal(false);
  /**
   * Dejé de seguir a este pid, y el panel sigue abierto por eso.
   *
   * No es lo mismo que cerrar: el texto que ya se leyó sigue ahí, que es justo
   * lo que el developer estaba leyendo cuando el proceso se fue. Lo que se corta
   * es la pregunta — un 404 del backend es terminal, el pid dejó de estar
   * trackeado y no va a volver, así que esperar otro tick no es esperar: es una
   * request que sólo puede volver a fallar.
   */
  readonly gone = signal(false);

  /** Para el `[hidden]` del `<section>`. `null` = cerrado. */
  readonly scope = computed<number | null>(() => this.pid());

  /** El pid que el "Ver logs" de la barra debería ofrecer, o `null` si no hay. */
  readonly firstLogPid = computed<number | null>(() => {
    const chosen = this.pid();
    const dead = chosen !== null && this.gone();
    if (chosen && !dead) return chosen;

    // El pid muerto se filtra también de la búsqueda por filas, no sólo del
    // atajo: las filas son un snapshot del último poll, así que pueden seguir
    // dibujando ese proceso arriba con su `log_file` hasta que llegue el estado
    // nuevo. Si sólo se filtrara el atajo, la búsqueda devolvería el mismo pid y
    // el botón quedaría habilitado igual.
    const row = this.rows().find(
      (r) => r.log_file && canShowLogs(r.state) && (!dead || r.pid !== chosen),
    );
    return row?.pid ?? null;
  });

  /**
   * Si el atajo de la barra tiene a quién abrir.
   *
   * Vive acá porque depende de `firstLogPid`, que es el que sabe del `gone`. La
   * barra lo pregunta con un `[disabled]` y no con un `?? null` en el template:
   * un pid 0 no existe, pero un `null` compared con `!= null` en un binding es
   * menos legible que un `canShowLogs` con nombre.
   */
  readonly hasFirstLog = computed<boolean>(() => this.firstLogPid() !== null);

  constructor() {
    // La conexión del log. Vive acá y no en la página porque el ciclo de vida que le
    // interesa es el del panel: mientras haya un pid mirado y no se haya cortado,
    // hay que estar escuchando. Con la página, el log seguiría conectado a un panel
    // que ya no existe.
    effect((onCleanup) => {
      const pid = this.pid();
      if (!pid || this.gone()) return;
      const subscription = this.workflow
        .streamLogs(pid, untracked(this.section))
        // `untracked` no es Prudencia decorativa: el handler escribe `text`,
        // `offset` y `loaded`, y si el efecto los trackeara, escribir un frame
        // dispararía el efecto otra vez, el efecto abriría otra conexión, y el
        // panel se comería sus propias streams en bucle. Es la regla de "un efecto
        // que abre algo no puede escribir lo que lee" — ver `docs/lenguaje-visual`.
        .subscribe((frame) => untracked(() => this.apply(frame)));
      onCleanup(() => subscription.unsubscribe());
    });
  }

  /**
   * "Ver logs" de una fila: filtra por ese recurso y abre el panel.
   *
   * El offset arranca en cero y la primera lectura pide el final: el backend
   * devuelve la cola del archivo y desde ahí sólo lo que se agrega. Pedir las
   * últimas 60 líneas en el primer poll y después el offset evita el solapamiento
   * entre lecturas.
   */
  open(row: WorkflowRow): void {
    const pid = row.pid ?? null;
    this.pid.set(pid);
    this.text.set('');
    this.offset.set(0);
    this.loaded.set(false);
    this.gone.set(false);
  }

  /**
   * "Ver logs" de la barra de resumen.
   *
   * La barra no es un recurso: no tiene fila ni botón propio, así que "ver logs"
   * sin más abre el primer recurso de la pantalla que tenga archivo.
   */
  openFirst(): void {
    const pid = this.firstLogPid();
    if (!pid) return;
    this.open({ pid } as WorkflowRow);
  }

  close(): void {
    this.pid.set(null);
    this.text.set('');
    this.offset.set(0);
    this.loaded.set(false);
    this.gone.set(false);
  }

  /**
   * Un frame del log, aplicado a la pantalla.
   *
   * `gone` corta la conexión en vez de marcar un error: el pid dejó de estar
   * trackeado y no va a volver, así que no hay nada que esperar. Es la misma regla
   * que antes —el 404 era terminal— pero llega como evento en vez de como el
   * error de una request que igual había que hacer.
   */
  private apply(frame: LogStreamEvent): void {
    if (frame.gone) {
      this.forgetLog(frame.pid);
      return;
    }
    if (frame.text) this.text.update((prev) => (prev + frame.text).slice(-MAX_CHARS));
    this.offset.set(frame.offset);
    this.loaded.set(true);
  }

  /**
   * El backend ya no conoce este pid: el panel deja de seguir y lo dice.
   *
   * Se chequea que sea *el pid que se está mostrando*: un frame puede llegar
   * después de que el developer abrió el panel de otro recurso, y que se vaya
   * el nuevo no puede depender de una noticia vieja.
   */
  private forgetLog(pid: number): void {
    if (this.pid() !== pid) return;
    this.gone.set(true);
    // Ya se respondió: sin esto el panel diría "Buscando logs…" para siempre en
    // el caso de un proceso que murió sin escribir una sola línea.
    this.loaded.set(true);
  }
}
