/**
 * Estado de paginación para una lista que ya está en memoria.
 *
 * Nace de una queja concreta: las tablas de la app volcaban todo de una vez. Con
 * 500 filas de un SELECT hay que scrollear tres pantallas para llegar a la última,
 * y con la tabla de sesiones se perdía de vista dónde estabas. Paginarlas es lo
 * obvio; lo que no es obvio es que **no haya que escribir estado en ningún lado
 * para que sea correcta**, y eso es lo que hace esta fábrica.
 *
 * Tres decisiones que son el motivo de que sea una fábrica y no un `slice()` en
 * cada template:
 *
 * **La página se recorta sola.** `current()` es `clamp(page, 1, pageCount)`, así
 * que si el filtro de la tabla reduce la lista de 9 páginas a 1, la vista cae en
 * la última página real en vez de quedar en blanco con "página 7 de 3" arriba. Un
 * `computed` no puede escribir un signal, así que en vez de un `effect` que
 * corrige el número, el número que se usa ya viene recortado. Menos código y el
 * estado guardado nunca queda desincronizado con lo que se ve.
 *
 * **El tamaño de página no descarta la posición.** Cambiar de 25 a 10 páginas deja
 * la primera fila a la vista en vez de saltar al principio: `firstIndex()` se
 * recalcula sobre el tamaño viejo, que es lo que el usuario está mirando.
 *
 * **Los índices salen absolutos.** Los `@for` que necesitan el índice real —la
 * tabla de diff de JSON, que marca filas por posición— usan `visibleIndices()`, no
 * `indexOf`. Con `indexOf` una fila duplicada marcaría la anterior.
 */
import { Signal, WritableSignal, computed, signal } from '@angular/core';

/** Los tamaños que se ofrecen. Chicos a propósito: el default es el segundo. */
export const PAGE_SIZES = [10, 25, 50, 100] as const;

/** 25 es el default porque muestra de sobra sin empujar la tabla fuera de pantalla. */
export const DEFAULT_PAGE_SIZE = 25;

export interface Pagination<T> {
  /** La página pedida. Puede quedar fuera de rango: usá `current()`. */
  readonly page: WritableSignal<number>;
  readonly pageSize: WritableSignal<number>;
  /** Cuántos elementos hay en total, sin paginar. */
  readonly total: Signal<number>;
  readonly pageCount: Signal<number>;
  /** La página real, siempre dentro de rango. */
  readonly current: Signal<number>;
  /** Los elementos de la página actual. */
  readonly visible: Signal<T[]>;
  /** Los índices absolutos de la página actual, para los `@for` que indexan. */
  readonly visibleIndices: Signal<number[]>;
  /** Índice del primer elemento de la página, base 0. */
  readonly firstIndex: Signal<number>;
  /** "1–25 de 500", o "" si no hay nada: una barra de paginación vacía no dice nada. */
  readonly rangeLabel: Signal<string>;
  /** "Página 2 de 9", sólo si hay más de una. */
  readonly pageLabel: Signal<string>;
  readonly canPrev: Signal<boolean>;
  readonly canNext: Signal<boolean>;
  setPageSize(size: number): void;
  goTo(page: number): void;
  next(): void;
  prev(): void;
  /** Volver a la primera. Se llama cuando cambia lo que filtra la tabla. */
  reset(): void;
}

/**
 * Create the pagination state for `source`, a signal holding the full list.
 *
 * `source` se lee por signal, no como array, para que cambiar los datos repinte
 * la tabla: un array plano no tiene de dónde enterarse.
 */
export function paginate<T>(source: () => T[], initialSize: number = DEFAULT_PAGE_SIZE): Pagination<T> {
  const page = signal(1);
  const pageSize = signal(initialSize);

  const total = computed(() => source().length);
  const pageCount = computed(() => Math.max(1, Math.ceil(total() / pageSize())));
  const current = computed(() => Math.min(Math.max(1, page()), pageCount()));
  const firstIndex = computed(() => (current() - 1) * pageSize());
  const visibleIndices = computed(() => {
    const from = firstIndex();
    const to = from + pageSize();
    return source().map((_, i) => i).slice(from, to);
  });
  const visible = computed(() => visibleIndices().map((i) => source()[i]));

  const rangeLabel = computed(() => {
    const n = total();
    if (!n) return '';
    const from = firstIndex() + 1;
    const to = Math.min(n, firstIndex() + pageSize());
    return `${from}–${to} de ${n}`;
  });
  const pageLabel = computed(() =>
    pageCount() > 1 ? `Página ${current()} de ${pageCount()}` : '',
  );

  return {
    page,
    pageSize,
    total,
    pageCount,
    current,
    visible,
    visibleIndices,
    firstIndex,
    rangeLabel,
    pageLabel,
    canPrev: computed(() => current() > 1),
    canNext: computed(() => current() < pageCount()),
    setPageSize(size: number): void {
      const next = Number(size);
      if (!Number.isFinite(next) || next <= 0 || next === pageSize()) return;
      // La posición se recalcula sobre el tamaño viejo para no perder la fila
      // que el usuario estaba mirando.
      const anchor = firstIndex();
      pageSize.set(next);
      page.set(Math.floor(anchor / next) + 1);
    },
    goTo(target: number): void {
      page.set(Math.min(Math.max(1, Math.round(target) || 1), pageCount()));
    },
    next(): void {
      if (current() < pageCount()) page.set(current() + 1);
    },
    prev(): void {
      if (current() > 1) page.set(current() - 1);
    },
    reset(): void {
      page.set(1);
    },
  };
}
