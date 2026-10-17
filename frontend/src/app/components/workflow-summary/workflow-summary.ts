/**
 * La barra de resumen al pie de cada sección de Workflow: los contadores y las dos
 * acciones que no pertenecen a ninguna fila.
 *
 * "Ver logs" y "Parar todo" son acciones de la **pantalla**, no de un recurso. Por
 * eso no viven en la grilla de tarjetas ni en la fila del catálogo, y por eso se
 * comparten: la cuenta es la misma en las cuatro secciones, y el texto de "Parar
 * todo" tiene que decir lo mismo en las cuatro.
 *
 * El componente no calcula nada: recibe el texto y las dos condiciones ya
 * resueltas. Quién es "el primer recurso con log" lo sabe el panel de logs, y qué
 * hay vivo lo sabe la página.
 */
import { Component, input, output } from '@angular/core';

@Component({
  selector: 'app-workflow-summary',
  templateUrl: './workflow-summary.html',
})
export class WorkflowSummary {
  /** "3 arriba · 1 levantando". */
  readonly text = input('');
  /** Sin nada vivo, "Parar todo" no tiene a quién alcanzar. */
  readonly canStopAll = input(false);
  /** Sin ningún recurso con archivo de log, el atajo no tiene a quién abrir. */
  readonly canShowLogs = input(false);

  readonly logs = output<void>();
  readonly stopAll = output<void>();
}
