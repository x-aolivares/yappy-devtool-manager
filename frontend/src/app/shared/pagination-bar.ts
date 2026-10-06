/**
 * La barra de paginación de una tabla.
 *
 * Presentacional y genérica como `app-notice-modal`: recibe un `Pagination<T>` y
 * dibuja los controles. No sabe de dónde viene la lista, qué se está paginando ni
 * cuántas filas hay en total — todo eso ya está resuelto en `paginate.ts`.
 *
 * **No se dibuja si hay una sola página.** Una barra con "Página 1 de 1" y dos
 * botones apagados es ruido: informa que no tiene nada que informar. Con una sola
 * página el rango ("1–25 de 25") tampoco aporta, así que la barra entera
 * desaparece y la tabla queda como estaba.
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
          @if (p().pageLabel(); as label) {
            <span class="muted">{{ label }}</span>
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

          <button type="button" class="secondary" [disabled]="!p().canPrev()" (click)="p().prev()">
            Anterior
          </button>
          <button type="button" class="secondary" [disabled]="!p().canNext()" (click)="p().next()">
            Siguiente
          </button>
        </div>
      </div>
    }
  `,
})
export class PaginationBarComponent {
  /** La lista que se pagina. Genérica para que el tipo no se pierda al pasarla. */
  readonly p = input.required<Pagination<unknown>>();

  protected readonly sizes = PAGE_SIZES;

  /**
   * Sólo hay barra cuando hay algo que paginar.
   *
   * Se calcula con `total` y no con `rangeLabel` porque el rango vacío es la otra
   * mitad de la misma idea: sin filas no hay ni barra ni etiqueta.
   */
  protected readonly show = computed(() => {
    const p = this.p();
    return p.total() > 0 && p.pageCount() > 1;
  });

  onSize(value: string): void {
    this.p().setPageSize(Number(value));
  }
}
