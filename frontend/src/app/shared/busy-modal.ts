/**
 * Modal de espera, para operaciones que dejan a la página en `busy`.
 *
 * Es hermano de `notice-modal` pero se comporta al revés, y esa diferencia es
 * el motivo de que sea otro componente y no el mismo con un flag:
 *
 * - No se cierra con el backdrop ni con Escape. El `notice-modal` sí, porque
 *   cerrarlo es dejar de mirarlo; acá el fondo está inactivo y un backdrop que
 *   cerrara mentiría sobre lo que está pasando. Lo que lo cierra es el botón, y
 *   no por decorado: aborta la petición con `core/cancel`.
 * - Anuncia el texto con `role="status"` en vez de tomar el foco. El foco se
 *   queda donde estaba (en el botón que disparó la búsqueda): moverlo a un
 *   diálogo que se va a cerrar solo manda al lector de pantalla a un elemento
 *   que desaparece, y deja el foco perdido en un árbol que cambió. Con
 *   `aria-live` el texto se anuncia igual sin ese problema. El botón de cancelar
 *   tampoco lo toma: se va con el modal, y el foco vuelve a donde estaba.
 *
 * El `role="alert"` de un modal de error no corresponde acá: no es una
 * interrupción, es un estado que la propia app inició y va a terminar.
 */
import { Component, effect, input, output } from '@angular/core';

@Component({
  selector: 'app-busy-modal',
  template: `
    @if (open()) {
      <div class="busy-modal__backdrop"></div>
      <div class="busy-modal" role="status" aria-live="polite">
        <div class="busy-modal__main">
          <span class="spinner" aria-hidden="true"></span>
          <span class="busy-modal__text">{{ message() }}</span>
        </div>
        @if (writes()) {
          <p class="busy-modal__hint">{{ writeHint }}</p>
        }
        <button type="button" class="secondary busy-modal__cancel" (click)="cancelled.emit()">
          {{ writes() ? writeLabel : readLabel }}
        </button>
      </div>
    }
  `,
})
export class BusyModalComponent {
  readonly open = input(false);
  readonly message = input('Trabajando…');
  /**
   * La operación en vuelo **escribe**. Cambia el rótulo del botón y agrega la
   * línea que dice lo que ese botón no puede prometer.
   *
   * Abortar el request corta la conexión, no el `REPLACE INTO` que el backend ya
   * está ejecutando: la escritura puede terminar igual y nadie va a avisar. Un
   * "Cancelar" sobre ese botón diría una cosa falsa —"no pasó"— y por eso acá
   * dice "Dejar de esperar", que es exactamente lo que hace.
   */
  readonly writes = input(false);
  readonly cancelled = output<void>();

  /** Lo que el botón de una escritura NO promete, dicho una vez y en todas. */
  protected readonly writeHint =
    'La escritura puede igual terminar: se corta la espera, no la operación.';
  protected readonly writeLabel = 'Dejar de esperar';
  protected readonly readLabel = 'Cancelar';

  /**
   * El elemento que tenía el foco cuando se abrió, para devolvérselo.
   *
   * El modal no toma el foco nunca, así que en el caso normal —el usuario ni
   * siquiera llegó al botón— el foco nunca se fue y no hay nada que hacer. Lo que
   * sí pasa es el caso de quien tabuló hasta "Cancelar": ese elemento se va con
   * el modal y el foco cae al `<body>`, que obliga a recorrer la página desde
   * arriba para volver al control que disparó la operación.
   */
  private wasFocused: HTMLElement | null = null;

  constructor() {
    effect(() => {
      if (this.open()) {
        this.wasFocused = (document.activeElement as HTMLElement | null) ?? null;
        return;
      }
      const back = this.wasFocused;
      this.wasFocused = null;
      // `document.body` es la señal de "el elemento con el foco ya no está": si
      // el botón de cancelar se llevó el foco al desmontarse, el navegador lo
      // devuelve al body.
      if (back && document.contains(back) && document.activeElement === document.body) {
        back.focus();
      }
    });
  }
}