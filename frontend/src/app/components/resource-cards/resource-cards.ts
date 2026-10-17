/**
 * La grilla de tarjetas de un recurso: Kafka, Servicios con destino propio y Base
 * de datos las dibujan con este componente.
 *
 * Las tres secciones eran el mismo bloque de markup con el rótulo del puerto
 * cambiado, y por eso se comparte: el botón que una fila ofrece no puede ser
 * distinto en una que en otra. "Una fila ajena no tiene botón de parar" es una
 * regla de seguridad —`kill_ssm()` trabaja sobre el tracker y no le importa quién
 * levantó qué—, y una regla de seguridad repetida tres veces es una regla que
 * alguien va a cambiar en una de las tres.
 *
 * El componente **no llama al backend**: emite lo que la fila pidió y lo decide
 * la página. Así el `busy` es de la página, que es la que sabe si hay una
 * operación en vuelo, y el log es el del panel compartido.
 */
import { Component, input, output } from '@angular/core';

import type { WorkflowResourceInfo } from '../../api-gen/models';
import {
  canOpen, canShowLogs, canStart, canStop, resourceMeta, startLabel,
  stateBadge, stateBadgeClass,
} from '../../core/resources';

@Component({
  selector: 'app-resource-cards',
  templateUrl: './resource-cards.html',
  styleUrl: './resource-cards.scss',
})
export class ResourceCards {
  readonly rows = input.required<readonly WorkflowResourceInfo[]>();
  /** While an operation is in flight, every action on every row waits. */
  readonly busy = input(false);

  /**
   * Cómo se dibuja el puerto.
   *
   * `local` — `:9092`. Es lo que muestran Kafka y Base de datos: el puerto local
   * es el único que el developer puede abrir.
   *
   * `remote` — `:9092 → :9094`. Sólo para los servicios con destino propio, que
   * son los únicos que traducen uno en otro y por lo tanto los únicos en los que
   * callar el puerto de destino esconde información.
   */
  readonly portMode = input<'local' | 'remote'>('local');

  /**
   * Lo que dice la grilla cuando no hay filas.
   *
   * Cada sección dice una cosa distinta —"no hay Kafka levantado" no es "no hay
   * una base de datos levantada"— así que lo pasa la página en vez de que el
   * componente adivine por `group`.
   */
  readonly emptyText = input('Acá no hay nada levantado.');

  readonly start = output<WorkflowResourceInfo>();
  readonly stop = output<WorkflowResourceInfo>();
  readonly logs = output<WorkflowResourceInfo>();
  /** A row the backend says has a known remedy. See `FIXES`. */
  readonly fix = output<WorkflowResourceInfo>();

  // Referencia directa a las funciones puras de `core/resources`: la regla de qué
  // botón se ofrece vive una sola vez en el repo.
  protected readonly meta = resourceMeta;
  protected readonly badge = stateBadge;
  protected readonly badgeClass = stateBadgeClass;
  protected readonly startText = startLabel;
  protected readonly canStartRow = canStart;
  protected readonly canOpenRow = canOpen;
  protected readonly canShowLogsRow = canShowLogs;
  protected readonly canStopRow = canStop;

  /**
   * The known remedies, and what each one is called.
   *
   * A name comes from the backend and a sentence lives here, because the backend is
   * the only side that understands the failure and this is the only side that knows
   * how to talk to a person. The pairing is explicit rather than a computed string
   * so that a fix the server sends and this table does not know is *ignored*, not
   * rendered as a button with an empty label.
   */
  private readonly fixes: Record<string, { button: string; text: string }> = {
    kafka_clean_storage: {
      button: 'Limpiar el broker',
      text: 'El storage de Kafka quedó bloqueado y por eso no arranca. Se puede limpiar y formatear de nuevo.',
    },
  };

  /** The remedy for this row, or `''` when there is none or it is not ours. */
  protected fixOf(row: WorkflowResourceInfo): string {
    return row.fix && this.fixes[row.fix] ? row.fix : '';
  }

  protected fixButton(fix: string): string {
    return this.fixes[fix]?.button ?? '';
  }

  protected fixText(fix: string): string {
    return this.fixes[fix]?.text ?? '';
  }

  protected canStop(row: WorkflowResourceInfo): boolean {
    return canStop(row.state, !!row.foreign);
  }

  protected trackRow(_index: number, row: WorkflowResourceInfo): string { return row.key; }
}
