/**
 * El estado de paginación, sin DOM.
 *
 * Estas pruebas fijan las tres cosas que hacen que una paginación sea correcta y
 * no sólo que se vea bien: que la página se recorte sola cuando la lista crece o se
 * achica, que cambiar el tamaño de página no tire la fila que se está mirando, y
 * que los índices que salen sean absolutos —porque hay un `@for` que marca filas
 * por posición y con índices de página desmarcaba la fila equivocada.
 *
 * Se afirma sobre `visible()`/`visibleIndices()`, no sobre el texto de la barra:
 * la leyenda es copy y el mecanismo es lo que hay que proteger.
 */
import { signal } from '@angular/core';
import { DEFAULT_PAGE_SIZE, PAGE_SIZES, paginate } from './paginate';

/** `n` filas numeradas 0..n-1, para poder distinguir "la fila 3" de "la tercera". */
function rows(n: number): number[] {
  return Array.from({ length: n }, (_, i) => i);
}

/**
 * La lista, como signal, con una forma de cambiarla.
 *
 * Un signal de verdad y no un `{ value }` con un getter: `paginate` calcula sus
 * `computed` sobre una signal, así que un objeto plano los dejaría sin
 * invalidar y el test pasaría probando nada —que es exactamente lo que pasó la
 * primera vez que se escribió esto.
 */
function sourceOf<T>(initial: T[]): { read: () => T[]; set: (v: T[]) => void } {
  const s = signal(initial);
  return { read: s.asReadonly(), set: (v: T[]) => s.set(v) };
}

/** Atajo: la lista ya lista para pasarle a `paginate`. */
function sourceOfRead<T>(initial: T[]): () => T[] {
  return sourceOf(initial).read;
}

describe('paginate', () => {
  it('parte la lista y deja el resto para las páginas siguientes', () => {
    const src = sourceOf(rows(60));
    const p = paginate(src.read, 25);

    expect(p.visible()).toEqual(rows(25));
    expect(p.total()).toBe(60);
    expect(p.pageCount()).toBe(3);
  });

  it('la última página corta en el tamaño que queda, sin rellenar de basura', () => {
    const src = sourceOf(rows(60));
    const p = paginate(src.read, 25);

    p.goTo(3);

    expect(p.visible()).toHaveLength(10);
    expect(p.visible()[0]).toBe(50);
    expect(p.visible()[9]).toBe(59);
  });

  it('una lista más chica que la página no inventa filas', () => {
    const p = paginate(sourceOfRead(rows(3)), 25);

    expect(p.visible()).toEqual([0, 1, 2]);
    expect(p.pageCount()).toBe(1);
    expect(p.canPrev()).toBe(false);
    expect(p.canNext()).toBe(false);
  });

  it('una lista vacía no rompe nada', () => {
    const p = paginate(sourceOfRead(rows(0)), 25);

    expect(p.visible()).toEqual([]);
    expect(p.visibleIndices()).toEqual([]);
    expect(p.total()).toBe(0);
    expect(p.pageCount()).toBe(1);
    expect(p.rangeLabel()).toBe('');
    expect(p.pageLabel()).toBe('');
  });

  describe('la página se recorta sola', () => {
    it('cuando la lista se achica, la vista cae en la última real', () => {
      const src = sourceOf(rows(200));
      const p = paginate(src.read, 10);

      p.goTo(20);
      expect(p.current()).toBe(20);

      // El filtro dejó 5 filas: la vista tiene que mostrar esas 5, no quedar
      // en blanco con "página 20 de 1" arriba.
      src.set(rows(5));

      expect(p.current()).toBe(1);
      expect(p.visible()).toHaveLength(5);
      expect(p.pageLabel()).toBe('');
      expect(p.canNext()).toBe(false);
      expect(p.canPrev()).toBe(false);
    });

    it('nunca deja la página en 0 aunque se pidan 0', () => {
      const p = paginate(sourceOfRead(rows(10)), 5);

      p.goTo(0);
      expect(p.current()).toBe(1);

      p.goTo(-7);
      expect(p.current()).toBe(1);
    });

    it('no deja avanzar más allá de la última', () => {
      const p = paginate(sourceOfRead(rows(30)), 10);

      p.goTo(3);
      p.next();

      expect(p.current()).toBe(3);
    });

    it('no deja retroceder antes de la primera', () => {
      const p = paginate(sourceOfRead(rows(30)), 10);

      p.prev();

      expect(p.current()).toBe(1);
    });
  });

  describe('cambiar el tamaño de página', () => {
    it('conserva la fila que se estaba mirando', () => {
      const p = paginate(sourceOfRead(rows(200)), 50);

      // Página 2 con 50 por página: arranca en la fila 50.
      p.goTo(2);
      expect(p.firstIndex()).toBe(50);

      p.setPageSize(10);

      // Con 10 por página la fila 50 es la primera de la página 6. Perderla
      // haría que cambiar el select salte la mitad de la tabla.
      expect(p.current()).toBe(6);
      expect(p.firstIndex()).toBe(50);
      expect(p.visible()[0]).toBe(50);
    });

    it('ignora un tamaño inválido en vez de dejar la página en 0', () => {
      const p = paginate(sourceOfRead(rows(100)), 25);

      p.setPageSize(0);
      p.setPageSize(-10);
      p.setPageSize(Number.NaN);

      expect(p.pageSize()).toBe(25);
      expect(p.visible().length).toBe(25);
    });
  });

  describe('los índices son absolutos', () => {
    it('la segunda página de un bloque de 10 arranca en 10, no en 0', () => {
      const p = paginate(sourceOfRead(rows(35)), 10);

      p.goTo(2);

      // Un `@for` que marca por posición necesita el índice de la lista completa:
      // con el de la página, marcar la segunda fila de la página 2 desmarcaría
      // la segunda de la página 1.
      expect(p.visibleIndices()).toEqual([10, 11, 12, 13, 14, 15, 16, 17, 18, 19]);
      expect(p.visible()).toEqual([10, 11, 12, 13, 14, 15, 16, 17, 18, 19]);
    });

    it('coincide con visible() siempre', () => {
      const p = paginate(sourceOfRead(rows(37)), 10);
      p.goTo(4);

      expect(p.visibleIndices().length).toBe(p.visible().length);
      expect(p.visibleIndices()[0]).toBe(30);
      expect(p.visible()).toHaveLength(7);
    });
  });

  describe('las etiquetas', () => {
    it('el rango cuenta desde 1, porque es una etiqueta para personas', () => {
      const p = paginate(sourceOfRead(rows(60)), 25);
      expect(p.rangeLabel()).toBe('1–25 de 60');

      p.goTo(3);
      expect(p.rangeLabel()).toBe('51–60 de 60');
    });

    it('la última página no se pasa del total', () => {
      const p = paginate(sourceOfRead(rows(53)), 25);
      p.goTo(3);
      expect(p.rangeLabel()).toBe('51–53 de 53');
    });

    it('"Página N de M" sólo aparece si hay más de una', () => {
      const uno = paginate(sourceOfRead(rows(5)), 25);
      expect(uno.pageLabel()).toBe('');

      const varios = paginate(sourceOfRead(rows(60)), 25);
      expect(varios.pageLabel()).toBe('Página 1 de 3');
      varios.goTo(3);
      expect(varios.pageLabel()).toBe('Página 3 de 3');
    });
  });

  describe('reset', () => {
    it('vuelve a la primera página', () => {
      const p = paginate(sourceOfRead(rows(200)), 25);
      p.goTo(7);

      p.reset();

      expect(p.current()).toBe(1);
      expect(p.firstIndex()).toBe(0);
    });
  });

  it('respeta el tamaño inicial y ofrece tamaños sensatos', () => {
    const p = paginate(sourceOfRead(rows(100)));

    expect(p.pageSize()).toBe(DEFAULT_PAGE_SIZE);
    // 25 es el default: muestra de sobra sin empujar la tabla fuera de pantalla.
    expect(DEFAULT_PAGE_SIZE).toBe(25);
    expect(PAGE_SIZES).toContain(DEFAULT_PAGE_SIZE);
    expect(PAGE_SIZES.every((n) => n > 0)).toBe(true);
  });
});
