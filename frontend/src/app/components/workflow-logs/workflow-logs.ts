/**
 * El panel de logs, compartido por las cuatro páginas de Workflow.
 *
 * Nació dentro de la pantalla única y hoy lo usan Kafka, Túneles, Servicios y Base
 * de datos. Es el componente que más vale la pena tener suelto: el poll del log es
 * una máquina de estados con tres salidas —sigue, se cortó, se cerró— y cada una
 * tiene un error detrás que ya se cometió una vez.
 *
 * **El panel sigue un pid porque el developer apretó "Ver logs", no porque la
 * pantalla esté mirando.** Con la fila sola —incluso arriba y con archivo— no sale
 * ninguna request. Por eso el poll vive adentro del componente y arranca con
 * `open()`, no con la fila.
 *
 * El `gone` es la mitad de la regla: el pid se sigue mostrando, pero ya no se
 * vuelve a preguntar por él. Sin ese corte, un proceso que murió deja el panel
 * golpeando `/logs` cada 1,5s para siempre — eso es lo que llenaba el log del
 * server de 404s.
 */
import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';

import { WorkflowService } from '../../core/services/workflow.service';
import type { WorkflowRow } from '../../core/workflow-section-page';
import { canShowLogs } from '../../core/resources';

/**
 * Cada cuánto se relee el log.
 *
 * Va más rápido que el poll del estado a propósito: el readiness check del
 * supervisor corre cada medio segundo, así que 1,5s es lo que tarda en verse un
 * túnel nuevo arriba en el log. Es más corto que el de la página porque acá el
 * costo es una request a un archivo local.
 */
const LOG_POLL_MS = 1500;

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
    // El poll del log. Vive acá y no en la página porque el ciclo de vida que le
    // interesa es el del panel: mientras haya un pid mirado y no se haya cortado,
    // hay que preguntar. Con la página, el log seguiría preguntando por un panel
    // que ya no existe.
    effect((onCleanup) => {
      const pid = this.pid();
      if (!pid || this.gone()) return;
      let timer: ReturnType<typeof setTimeout>;
      const tick = (): void => {
        void this.appendLog(pid);
        timer = setTimeout(tick, LOG_POLL_MS);
      };
      tick();
      onCleanup(() => clearTimeout(timer));
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

  private async appendLog(pid: number): Promise<void> {
    try {
      // El `offset` se lee **sin trackear**, y no es una casualidad: `appendLog` se
      // llama de forma síncrona dentro del `effect` del poll, así que un
      // `this.offset()` a secas dejaba que el effect dependiera de él. Cada vuelta
      // escribía el offset, el effect volvía a correr, y el panel se comía sus
      // propios polls en bucle: cien requests en el tiempo de una.
      //
      // El offset es el acumulador de la lectura, no una entrada del poll. Lo que
      // decide cuándo preguntar es el pid y el `gone`, y ésos sí se trackean.
      const tail = await this.workflow.log(pid, untracked(this.offset));
      if (tail.text) this.text.update((prev) => (prev + tail.text).slice(-MAX_CHARS));
      this.offset.set(tail.offset);
      this.loaded.set(true);
    } catch (err) {
      if (this.isUntracked(err)) {
        this.forgetLog(pid);
        return;
      }
      // Todo lo demás es transitorio — un corte de red, el server reiniciándose
      // — y no es un fallo de la pantalla. El panel no se cierra y el próximo
      // tick reintenta solo.
      this.loaded.set(true);
    }
  }

  /**
   * Si este 404 no se puede volver a resolver.
   *
   * El backend responde 404 en dos casos distintos y sólo uno es terminal: el
   * pid dejó de estar trackeado (`routes/workflow.py`, 404 "No hay ningún
   * proceso trackeado con ese pid"), o el proceso existe pero no tiene archivo
   * de log. Los dos son igual de definitivos para *este* panel — no hay nada que
   * volver a pedir — así que los dos cortan el poll. Lo que no corta es un 500 o
   * un status 0: ahí el proceso puede volver a contestarte en el tick siguiente.
   */
  private isUntracked(err: unknown): boolean {
    return err instanceof HttpErrorResponse && err.status === 404;
  }

  /**
   * El backend ya no conoce este pid: el panel deja de pedir y lo dice.
   *
   * Se chequea que el 404 sea *del pid que se está mostrando*: la request de
   * un panel puede volver después de que el developer abrió el de otro recurso, y
   * un 404 tardío no puede matar el seguimiento del nuevo.
   */
  private forgetLog(pid: number): void {
    if (this.pid() !== pid) return;
    this.gone.set(true);
    // Ya se respondió: sin esto el panel diría "Buscando logs…" para siempre en
    // el caso de un proceso que murió sin escribir una sola línea.
    this.loaded.set(true);
  }

  /**
   * Expuesto para tests: un tick del poll sin esperar 1,5s.
   *
   * Lo que se verifica es que *no* vuelva a preguntar, y eso no se ve sin
   * dispararlo: el `setTimeout` real no es algo que un test pueda avanzar sin
   * meterse con timers que los otros tests también usan.
   */
  pollForTest(): void {
    const pid = this.pid();
    if (pid && !this.gone()) void this.appendLog(pid);
  }
}
