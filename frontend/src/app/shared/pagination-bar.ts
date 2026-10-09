/**
 * La barra de paginación de una tabla.
 *
 * Presentacional y genérica como `app-notice-modal`: recibe un `Pagination<T>` y
 * dibuja los controles. No sabe de dónde viene la lista, qué se está paginando ni
 * cuántas filas hay en total — todo eso ya está resuelto en `paginate.ts`.
 *
 * **El tamaño de página siempre se puede cambiar; la navegación, no siempre hace
 * falta.** La barra se escondía entera cuando había una sola página, y eso tapaba
 * justo el control que hacía falta: con 7 filas no hay paginación, así que el
 * `<select>` desaparecía — pero "una sola página" de 25 filas puede seguir
 * desbordando la pantalla, y bajarla a 5 es lo único que la deja entera a la vista.
 * Un control que desaparece justo cuando lo necesitás no es un control.
 *
 * Así que ahora: con una sola página quedan el rango y el selector, y se van la
 * etiqueta de página y los dos botones, que no tienen a dónde ir. Sin filas, no hay
 * barra: ahí no hay nada que ajustar.
 *
 * El `<select>` de filas lleva su propia etiqueta, como el resto de los selects
 * pelados de la app — la regla del `.field-label` es sólo para cuando el control
 * de adentro no trae etiqueta propia.
 */
import { Component, computed, inject, input } from '@angular/core';
import { PAGE_SIZES, Pagination } from './paginate';

@Component({
  selector: 'app-pagination-bar',
  template: `
    @if (show()) {
      <div class="table-pagination">
        <span class="muted table-pagination__count">{{ p().rangeLabel() }}</span>

        <div class="actions table-pagination__controls">
          @if (showNav()) {
            @if (p().pageLabel(); as label) {
              <span class="muted">{{ label }}</span>
            }
          }

          <label class="table-pagination__size">
            <span class="muted">Filas</span>
            <select
              [value]="p().pageSize()"
              [attr.aria-label]="'Filas por página'"
              (change)="onSize($any($event.target).value)"
            >
              @for (n of sizes; track n) {
                <option [value]="n" [selected]="p().pageSize() === n">{{ n }}</option>
              }
            </select>
          </label>

          @if (showNav()) {
            <button type="button" class="secondary" [disabled]="!p().canPrev()" (click)="p().prev()">
              Anterior
            </button>
            <button
              type="button"
              class="secondary"
              [disabled]="!p().canNext()"
              (click)="p().next()"
            >
              Siguiente
            </button>
          }
        </div>
      </div>
    }
  `,
})
export class PaginationBar {
  /** La lista que se pagina. Genérica para que el tipo no se pierda al pasarla. */
  readonly p = input.required<Pagination<unknown>>();

  protected readonly sizes = PAGE_SIZES;

  /**
   * Sólo hay barra cuando hay filas que ajustar.
   *
   * Se calcula con `total` y no con `rangeLabel` porque el rango vacío es la otra
   * mitad de la misma idea: sin filas no hay ni barra ni etiqueta.
   */
  protected readonly show = computed(() => this.p().total() > 0);

  /**
   * La navegación sólo aparece si hay algo que recorrer.
   *
   * "Página 1 de 1" y dos botones que nunca se encienden son ruido puro. El
   * selector de tamaño se queda igual, que es el que sí tiene algo que hacer.
   */
  protected readonly showNav = computed(() => this.p().pageCount() > 1);

  onSize(value: string): void {
    this.p().setPageSize(Number(value));
  }
}
