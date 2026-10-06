/**
 * El filtro de tablas, sin DOM.
 *
 * Lo que se protege acá es que **matchea por celda, no por clave**. La tentación
 * obvia es filtrar con un `JSON.stringify` de la fila y listo; eso hace que
 * buscar `id` encuentre todas las filas, porque todas tienen una clave `id`, y que
 * una `Date` se compare como `[object Date]`.
 */
import { signal } from '@angular/core';
import { searchable } from './table-search';

interface Row {
  readonly name: string;
  readonly kind: string;
  readonly exists: boolean;
  readonly tags: string[];
}

const TABLA: Row = { name: 'yappy_payment', kind: 'tabla', exists: true, tags: [] };
const SP: Row = { name: 'sp_sync_payment', kind: 'procedure', exists: false, tags: ['diaria'] };
const OTRA: Row = { name: 'audit_log', kind: 'tabla', exists: false, tags: [] };
const TODAS: Row[] = [TABLA, SP, OTRA];

function cells(row: Row): string[] {
  return [row.name, row.kind, row.exists ? 'Sí' : 'No, se crea', ...row.tags];
}

function filtroSobre(filas: Row[] = TODAS) {
  return searchable(() => filas, cells);
}

describe('searchable', () => {
  it('sin texto deja todo', () => {
    const f = filtroSobre();

    expect(f.filtered()).toEqual(TODAS);
  });

  it('matchea contra cualquier celda, no sólo la primera', () => {
    const f = filtroSobre();

    // "procedure" está en la segunda celda de SP: un filtro que mirara sólo el
    // nombre no lo encontraría.
    f.query.set('procedure');
    expect(f.filtered()).toEqual([SP]);

    // Y "Sí" sólo está en la celda de existencia, que es la tercera.
    f.query.set('Sí');
    expect(f.filtered()).toEqual([TABLA]);
  });

  it('no matchea por el nombre de la clave', () => {
    const f = filtroSobre();

    // Todas las filas tienen `name`, `kind`, `exists` y `tags`. Con un
    // `JSON.stringify` de la fila, buscar cualquiera de esas palabras traería
    // todo y el filtro no serviría para nada.
    for (const clave of ['name', 'kind', 'exists', 'tags']) {
      f.query.set(clave);
      expect(f.filtered()).toEqual([]);
    }
  });

  it('no distingue mayúsculas', () => {
    const f = filtroSobre();

    f.query.set('PAYMENT');

    expect(f.filtered()).toEqual([TABLA, SP]);
  });

  it('ignora los espacios de los dos lados', () => {
    const f = filtroSobre();

    f.query.set('  audit  ');
    expect(f.filtered()).toEqual([OTRA]);

    // Sólo espacios es "sin filtro", no "nada coincide": una caja con un espacio
    // arriba no debería vaciar la tabla.
    f.query.set('   ');
    expect(f.filtered()).toEqual(TODAS);
  });

  it('matchea un fragmento en el medio', () => {
    const f = filtroSobre();

    f.query.set('yment');

    expect(f.filtered()).toEqual([TABLA, SP]);
  });

  it('no encuentra nada devuelve la lista vacía, no todas', () => {
    const f = filtroSobre();

    f.query.set('no-existe-esta-cosa');

    expect(f.filtered()).toEqual([]);
  });

  it('reacciona a que cambien las filas de abajo', () => {
    const filas = signal<Row[]>(TODAS);
    const f = searchable(() => filas(), cells);

    f.query.set('payment');
    expect(f.filtered()).toHaveLength(2);

    filas.set([OTRA]);

    // El filtro se recalcula: si guardara un array derivado, mostraría las dos
    // filas que ya no existen.
    expect(f.filtered()).toEqual([]);
  });
});
