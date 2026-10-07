/**
 * Modal de espera, para operaciones que dejan a la página en `busy`.
 *
 * Es hermano de `notice-modal` pero se comporta al revés, y esa diferencia es
 * el motivo de que sea otro componente y no el mismo con un flag:
 *
 * - No se cierra. El `notice-modal` se cierra con el backdrop, con Escape y con
 *   el botón; acá ninguna de las tres cosas aplica, porque la operación no se
 *   cancela —sigue corriendo aunque el usuario se arrepienta del click—. Un
 *   backdrop que cerrara un modal de espera mentiría sobre lo que está pasando.
 * - No tiene botón de acción, porque no hay nada que decidir.
 * - Anuncia el texto con `role="status"` en vez de tomar el foco. El foco se
 *   queda donde estaba (en el botón que disparo la búsqueda): moverlo a un
 *   diálogo que se va a cerrar solo manda al lector de pantalla a un elemento
 *   que desaparece, y deja el foco perdido en un árbol que cambió. Con
 *   `aria-live` el texto se anuncia igual sin ese problema.
 *
 * El `role="alert"` de un modal de error no corresponde acá: no es una
 * interrupción, es un estado que la propia app inició y va a terminar.
 */
import { Component, input } from '@angular/core';

@Component({
  selector: 'app-busy-modal',
  template: `
    @if (open()) {
      <div class="busy-modal__backdrop"></div>
      <div class="busy-modal" role="status" aria-live="polite">
        <span class="spinner" aria-hidden="true"></span>
        <span class="busy-modal__text">{{ message() }}</span>
      </div>
    }
  `,
})
export class BusyModalComponent {
  readonly open = input(false);
  readonly message = input('Trabajando…');
}