/**
 * Filtrar una tabla por el texto de sus columnas.
 *
 * Es la otra mitad de `paginate.ts` y se componen en el mismo orden: **primero el
 * filtro, después la paginación**. Al revés, buscar algo que está en la fila 60
 * daría cero resultados en la página 1 y lo encontraría en la página 3 — sin
 * ningún error, sólo una lista que no es la que se buscó.
 *
 * El filtro matchea contra **todas** las celdas de la fila, no contra una sola
 * columna, y es lo que hace que sirva para cosas distintas sin cambiar de
 * control: en Sincronizar schema, buscando `payment` encuentra la tabla
 * `yappy_payment` y el procedure `sp_sync_payment` por igual, y en la grilla de
 * SQL encuentra un valor que está en cualquier columna.
 *
 * El texto se pasa como un `string[]` de celdas yaBusinesses a string y se
 * compara en minúsculas. Se hace así a propósito, no con un `JSON.stringify` de
 * la fila: los nombres de las claves ensuciarían el resultado ("buscar `id`
 * encuentra todo") y una `Date` se compararía como `[object Date]`.
 */
import { Component, Signal, computed, input, output, signal } from '@angular/core';

/**
 * Create a filter over `source`, with `toCells` saying what text each row offers.
 *
 * `toCells` se llama una vez por fila y por cambio del filtro, no en cada tecla
 * con la fila ya normalizada: es trabajo barato pero no gratis con 500 filas, y lo
 * que evita es que la función tenga que ser pura-para-nada.
 */
export function searchable<T>(
  source: () => T[],
  toCells: (row: T) => string[],
): { query: ReturnType<typeof signal<string>>; filtered: Signal<T[]> } {
  const query = signal('');
  const filtered = computed(() => {
    const q = query().trim().toLowerCase();
    const all = source();
    if (!q) return all;
    return all.filter((row) => toCells(row).some((cell) => cell.toLowerCase().includes(q)));
  });
  return { query, filtered };
}

/**
 * The search box.
 *
 * Presentacional, como `pagination-bar`. Un `<input type="search">` pelado con su
 * `<label>` — la regla del `.field-label` es sólo para cuando el control de
 * adentro no trae etiqueta propia, y un input no la trae.
 *
 * El `id` es `input.required` porque hay varias tablas en la misma pantalla y dos
 * `<label for>` apuntando al mismo input rompen el lector de pantalla: los dos
 * anunciarían la misma etiqueta.
 */
@Component({
  selector: 'app-table-search',
  template: `
    <div class="table-search">
      <label class="table-search__label" [for]="controlId()">{{ label() }}</label>
      <input
        [id]="controlId()"
        type="search"
        [value]="query()"
        [placeholder]="placeholder()"
        [attr.aria-label]="label()"
        (input)="changed.emit($any($event.target).value)"
      />
    </div>
  `,
})
export class TableSearchComponent {
  readonly controlId = input.required<string>();
  readonly query = input('');
  readonly label = input('Filtrar');
  readonly placeholder = input('Escribí para filtrar…');
  readonly changed = output<string>();
}
