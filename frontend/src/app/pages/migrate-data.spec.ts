import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { MigrateDataPage } from './migrate-data';
import { DbService } from '../core/services/db.service';
import { EnvironmentService } from '../core/services/environment.service';
import { DateColumnInfo, DateColumnsResponse, TableMigrateRequest } from '../api-gen/models';

const TABLES = ['orders', 'lines', 'config'];

const DATE_COLUMNS: Record<string, { columns: Array<{ name: string; type: string }> }> = {
  orders: {
    columns: [
      { name: 'created_at', type: 'datetime' },
      { name: 'updated_at', type: 'timestamp' },
    ],
  },
  lines: { columns: [{ name: 'created_at', type: 'date' }] },
  // Una tabla sin DATE/DATETIME/TIMESTAMP: no se puede filtrar.
  config: { columns: [] },
};

const BASE: Record<string, any> = {
  env_b: 'dev',
  env_a: 'local',
  dry_run: false,
  tables: [
    {
      schema_name: 'yappy',
      table_name: 'orders',
      alias: '',
      target_schema: 'yappy',
      target_table: 'orders',
      select_sql:
        "SELECT * FROM `yappy`.`orders`\nWHERE `created_at` >= '2026-03-01 00:00:00' " +
        "AND `created_at` < '2026-03-02 00:00:00'",
      row_count: 120,
      replaced: 118,
      skipped_columns: [],
      ok: true,
      error: '',
    },
    {
      schema_name: 'yappy',
      table_name: 'config',
      alias: '',
      target_schema: 'yappy',
      target_table: 'config',
      select_sql: 'SELECT * FROM `yappy`.`config`',
      row_count: 0,
      replaced: 0,
      skipped_columns: ['legacy_id', 'note'],
      ok: false,
      error: 'La tabla yappy.config no existe en el destino',
    },
  ],
  notes: [
    'Se migran 2 tabla(s) de yappy con REPLACE INTO.',
    'REPLACE INTO no borra nada: las filas del destino que están fuera de la ventana siguen ahí.',
    'La tabla tiene que existir en el destino: esta migración no la crea.',
    'No hay transacción: una falla no deshace lo que ya se copió.',
  ],
  ok_count: 1,
  err_count: 1,
};

// El stub es mutable y module-level a propósito: `TestBed` se configura una vez por
// describe, así que cada test cambia la respuesta desde acá en vez de reconfigurar.
let tables: string[] = [...TABLES];
let dateColumnsFor: (env: string, schema: string, table: string) => Promise<DateColumnsResponse> =
  (env, schema, table) => Promise.resolve({ env, schema_name: schema, table_name: table, ...DATE_COLUMNS[table] });
let migrateResponse: Record<string, any> = BASE;
let sent: TableMigrateRequest[] = [];
let dateColumnCalls: Array<{ env: string; schema: string; table: string }> = [];

function mockProviders() {
  return [
    {
      provide: EnvironmentService,
      useValue: { list: () => Promise.resolve({ environments: [] }) },
    },
    {
      provide: DbService,
      useValue: {
        listSchemas: () => Promise.resolve({ schemas: ['yappy', 'otro'] }),
        listObjects: (env: string, schema: string) =>
          Promise.resolve({ env, schema_name: schema, object_type: 'table', objects: [...tables] }),
        dateColumns: (env: string, schema: string, table: string) => {
          dateColumnCalls.push({ env, schema, table });
          return dateColumnsFor(env, schema, table);
        },
        migrateTables: (req: TableMigrateRequest) => {
          sent.push(req);
          return Promise.resolve(migrateResponse);
        },
      },
    },
  ];
}

async function setup(): Promise<void> {
  tables = [...TABLES];
  dateColumnsFor = (env, schema, table) =>
    Promise.resolve({
      env,
      schema_name: schema,
      table_name: table,
      // Las tablas que un test genera al vuelo (`tabla_0`, `tabla_1`, ...) no
      // están en DATE_COLUMNS; les damos una columna de fecha para que el
      // componente tenga algo real con qué trabajar.
      ...(DATE_COLUMNS[table] ?? { columns: [{ name: 'created_at', type: 'datetime' }] }),
    });
  migrateResponse = BASE;
  sent = [];
  dateColumnCalls = [];
  await TestBed.configureTestingModule({
    imports: [MigrateDataPage],
    providers: [provideRouter([]), ...mockProviders()],
  }).compileComponents();
}

async function settle(fixture: ComponentFixture<MigrateDataPage>): Promise<void> {
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

/** Crea la página con origen, destino y esquema ya cargados. */
async function ready(): Promise<{
  fixture: ComponentFixture<MigrateDataPage>;
  comp: any;
  el: HTMLElement;
}> {
  const fixture = TestBed.createComponent(MigrateDataPage);
  const comp = fixture.componentInstance as any;
  comp.envB.set('dev');
  await settle(fixture);
  comp.envA.set('local');
  await settle(fixture);
  comp.schema.set('yappy');
  await settle(fixture);
  return { fixture, comp, el: fixture.nativeElement as HTMLElement };
}

function button(el: HTMLElement, label: string): HTMLButtonElement {
  return Array.from(el.querySelectorAll('button')).find((b) =>
    (b.textContent ?? '').includes(label),
  ) as HTMLButtonElement;
}

function optionsOf(el: HTMLElement, table: string): string[] {
  const select = el.querySelector(`#migrate-date-column-${table}`) as HTMLSelectElement | null;
  return [...(select?.options ?? [])].map((o) => (o.textContent ?? '').trim());
}

/**
 * Marca una tabla **y deja la ventana global lista para enviar**.
 *
 * No es una comodidad: marcar una tabla le pone por defecto la primera columna
 * de fecha, y columna elegida sin ventana no es enviable — significaría "sin
 * filtro", o sea la tabla entera. Un test que quiere llegar al submit tiene que
 * pasar por una ventana, como el usuario.
 *
 * El filtro es de la página, así que se arma una vez y todas las tablas marcadas
 * lo reciben.
 */
async function marcarConRango(
  comp: any,
  fixture: ComponentFixture<MigrateDataPage>,
  table = 'orders',
  from = '2026-03-01',
  to = '2026-03-31',
): Promise<void> {
  comp.dateMode.set('range');
  comp.dateFrom.set(from);
  comp.dateTo.set(to);
  comp.toggle(table, true);
  await settle(fixture);
}

describe('MigrateDataPage tablas y columnas de fecha', () => {
  beforeEach(setup);

  it('lista las tablas del origen en la tabla de filtros', async () => {
    const { el } = await ready();

    // Una fila por tabla, con la casilla en la primera celda. Se afirma sobre
    // las casillas y la fila, no sobre el título del panel: el título es copy y
    // cambia con el rediseño, la fila es la estructura.
    expect(el.querySelector('#migrate-table-orders')).not.toBeNull();
    expect(el.querySelector('#migrate-table-config')).not.toBeNull();
    expect(el.querySelectorAll('table.filter-table tbody tr').length).toBe(3);
    expect(
      el.querySelectorAll('table.filter-table tbody tr td:first-child input[type="checkbox"]')
        .length,
    ).toBe(3);
  });

  it('una fila sin marcar muestra que su filtro no aplica', async () => {
    const { el } = await ready();

    // La fila existe igual, con la casilla disponible, pero sin celdas de filtro:
    // las columnas de fecha se piden cuando la tabla entra en la selección.
    const filas = [...el.querySelectorAll('table.filter-table tbody tr')];
    const sinMarcar = filas.filter((f) => f.classList.contains('is-off'));
    expect(sinMarcar.length).toBe(3);
    expect(sinMarcar[0].querySelector('#migrate-date-column-orders')).toBeNull();
  });

  it('no pregunta las columnas de fecha hasta que la tabla se marca', async () => {
    const { fixture, comp } = await ready();
    expect(dateColumnCalls).toHaveLength(0);

    comp.toggle('orders', true);
    await settle(fixture);

    expect(dateColumnCalls).toEqual([{ env: 'dev', schema: 'yappy', table: 'orders' }]);
  });

  it('el selector sólo ofrece las columnas reales de la tabla, más "Todo"', async () => {
    const { fixture, comp, el } = await ready();

    comp.toggle('orders', true);
    await settle(fixture);

    expect(optionsOf(el, 'orders')).toEqual([
      'Todo (sin filtro)',
      'created_at (datetime)',
      'updated_at (timestamp)',
    ]);
    // Sólo esas tres: `id` es una columna de la tabla pero no de fecha, y un
    // `<select>` no admite texto libre.
    expect(el.querySelectorAll('#migrate-date-column-orders option')).toHaveLength(3);
    // Y la primera es la elegida por defecto.
    expect(comp.filters().orders.column).toBe('created_at');
  });

  it('una tabla sin columnas de fecha avisa que se migra completa y no muestra el filtro', async () => {
    const { fixture, comp, el } = await ready();

    comp.toggle('config', true);
    await settle(fixture);

    // El aviso va en la fila, corto: la celda de una tabla no es lugar para un
    // párrafo. Lo que importa es que la fila no ofrece columna: sin ella no hay
    // sobre qué aplicar la ventana, y la tabla se migra completa.
    expect(el.textContent).toContain('Sin columnas de fecha');
    expect(el.querySelector('#migrate-date-column-config')).toBeNull();
    expect(comp.filters().config.column).toBe('');
    expect(comp.selection()[0]).toEqual({
      table: 'config',
      date_column: null,
      date_from: null,
      date_to: null,
    });
  });

  it('no deja escribir un nombre de columna que no sea de la tabla', async () => {
    const { fixture, comp } = await ready();
    await marcarConRango(comp, fixture);

    // El `<select>` sólo ofrece columnas reales, así que `usuario_id` no llega ni
    // a la validación: si llegara, no se cuela en el SQL.
    comp.setColumn('orders', 'usuario_id');
    await settle(fixture);

    expect(comp.filters().orders.column).toBe('');
    expect(comp.selection()[0].date_column).toBeNull();
  });

  it('la fila no tiene ya los controles de fecha: viven arriba de la tabla', async () => {
    const { fixture, comp, el } = await ready();
    await marcarConRango(comp, fixture);

    // La fila conserva su columna —que sí es por tabla— pero no su ventana. Si
    // estos ids vuelven a aparecer, el filtro volvió a ser por fila.
    expect(el.querySelector('#migrate-date-column-orders')).not.toBeNull();
    expect(el.querySelector('#migrate-data-from')).not.toBeNull();
    expect(el.querySelector('#migrate-day-orders')).toBeNull();
    expect(el.querySelector('#migrate-from-orders')).toBeNull();
    expect(el.querySelector('#migrate-to-orders')).toBeNull();
  });

  it('con "Todo" la tabla se manda entera aunque la ventana esté cargada', async () => {
    const { fixture, comp } = await ready();
    await marcarConRango(comp, fixture);

    comp.setColumn('orders', '');
    await settle(fixture);

    expect(comp.selection()[0]).toEqual({
      table: 'orders',
      date_column: null,
      date_from: null,
      date_to: null,
    });
  });

  it('los campos de fecha muestran los límites cargados', async () => {
    const { fixture, comp, el } = await ready();
    await marcarConRango(comp, fixture);

    expect((el.querySelector('#migrate-data-from') as HTMLInputElement).value).toBe('2026-03-01');
    expect((el.querySelector('#migrate-data-to') as HTMLInputElement).value).toBe('2026-03-31');
  });

  it('desmarcar y remarkar no vuelve a preguntar las columnas', async () => {
    const { fixture, comp } = await ready();
    await marcarConRango(comp, fixture);
    expect(dateColumnCalls).toHaveLength(1);

    comp.toggle('orders', false);
    await settle(fixture);
    comp.toggle('orders', true);
    await settle(fixture);

    expect(dateColumnCalls).toHaveLength(1);
    expect(comp.includedTables()).toEqual(['orders']);
  });

  it('"Todas" marca la lista entera y pregunta por cada tabla', async () => {
    const { fixture, comp } = await ready();

    comp.toggleAll(true);
    await settle(fixture);
    // La ventana es una sola para la migración entera: alcanza con cargarla una vez.
    comp.dateMode.set('month');
    comp.month.set('2026-03');
    await settle(fixture);

    expect(comp.includedTables()).toEqual(TABLES);
    expect(dateColumnCalls.map((c) => c.table)).toEqual(TABLES);
    expect(comp.canSubmit()).toBe(true);
  });

  it('no deja enviar una tabla cuyas columnas de fecha no se pudieron leer', async () => {
    dateColumnsFor = () => Promise.reject(new Error('boom'));
    const { fixture, comp, el } = await ready();

    comp.toggle('orders', true);
    await settle(fixture);

    // El detalle del error vive en un modal, no en la celda: era un párrafo de
    // doscientos caracteres con un `border-left` de bloque adentro de un `<td>`.
    expect(el.textContent).toContain('No se pudieron leer las columnas de fecha de orders');
    expect(el.querySelector('.notice-modal')).not.toBeNull();
    // En la fila queda sólo el marcador corto.
    expect(el.querySelector('table.filter-table .table-alert')).not.toBeNull();
    // Mandarla sin filtro la copiaría entera: eso se frena acá, no en el backend.
    expect(comp.canSubmit()).toBe(false);
    expect(comp.submitHint()).toContain('columnas de fecha');
    expect(button(el, 'Migrar a').disabled).toBe(true);

    comp.simulate();
    await settle(fixture);

    expect(sent).toHaveLength(0);
  });

  it('cerrar el modal no destraba nada: el error sigue frenando', async () => {
    dateColumnsFor = () => Promise.reject(new Error('boom'));
    const { fixture, comp, el } = await ready();

    comp.toggle('orders', true);
    await settle(fixture);
    expect(el.querySelector('.notice-modal')).not.toBeNull();

    comp.dismissNotice();
    await settle(fixture);

    expect(comp.noticeOpen()).toBe(false);
    expect(el.querySelector('.notice-modal')).toBeNull();
    // El estado de error sigue en la tabla: cerrar el aviso es sólo cerrarlo.
    expect(comp.failedTables().map((f: { table: string }) => f.table)).toEqual(['orders']);
    expect(comp.canSubmit()).toBe(false);
  });

  it('el marcador de la fila vuelve a abrir el modal', async () => {
    dateColumnsFor = () => Promise.reject(new Error('boom'));
    const { fixture, comp, el } = await ready();

    comp.toggle('orders', true);
    await settle(fixture);
    comp.dismissNotice();
    await settle(fixture);

    (el.querySelector('table.filter-table .table-alert') as HTMLButtonElement).click();
    await settle(fixture);

    expect(comp.noticeOpen()).toBe(true);
    expect(el.querySelector('.notice-modal')).not.toBeNull();
  });

  it('un fallo nuevo vuelve a abrir el modal, uno ya cerrado no', async () => {
    dateColumnsFor = () => Promise.reject(new Error('boom'));
    const { fixture, comp } = await ready();

    comp.toggle('orders', true);
    await settle(fixture);
    comp.dismissNotice();
    await settle(fixture);
    expect(comp.noticeOpen()).toBe(false);

    // Desmarcar y volver a marcar no vuelve a preguntar (las columnas ya se
    // pidieron y fallaron), así que el aviso no se reabre solo.
    comp.toggle('orders', false);
    comp.toggle('orders', true);
    await settle(fixture);
    expect(comp.noticeOpen()).toBe(false);

    // Pero una tabla que falla ahora sí lo abre: es un fallo nuevo.
    comp.toggle('lines', true);
    await settle(fixture);
    expect(comp.noticeOpen()).toBe(true);
  });
});

describe('MigrateDataPage la tabla se pagina', () => {
  beforeEach(setup);

  async function muchasTablas(n: number) {
    tables = Array.from({ length: n }, (_, i) => `tabla_${i}`);
    return ready();
  }

  it('con muchas tablas muestra una sola página', async () => {
    const { el } = await muchasTablas(60);

    expect(el.querySelectorAll('table.filter-table tbody tr').length).toBe(25);
    expect(el.querySelector('.table-pagination')?.textContent).toContain('1–25 de 60');
  });

  it('el contador de marcadas habla del total, no de la página', async () => {
    const { fixture, comp, el } = await muchasTablas(60);

    comp.toggleAll(true);
    await settle(fixture);
    comp.page.next();
    await settle(fixture);

    // La fila desmarcada está en la página 2. Si el contador dijera "24 de 25" el
    // usuario no sabría que hay 35 marcadas fuera de la pantalla.
    comp.toggle('tabla_30', false);
    await settle(fixture);

    expect(el.textContent).toContain('59 de 60 marcadas');
  });

  it('la migración manda todas las marcadas aunque no estén a la vista', async () => {
    // Lo que no puede pasar nunca: `selection` no está paginado, así que marcar
    // 60 tablas y ver 25 tiene que migrar las 60.
    const { fixture, comp } = await muchasTablas(60);

    comp.toggleAll(true);
    await settle(fixture);
    comp.page.next();
    await settle(fixture);
    comp.toggle('tabla_30', false);
    await settle(fixture);
    comp.dateMode.set('month');
    comp.month.set('2026-03');
    await settle(fixture);

    comp.simulate();
    await settle(fixture);

    expect(sent[0].tables).toHaveLength(59);
    expect(sent[0].tables.map((t: { table: string }) => t.table)).not.toContain('tabla_30');
  });

  it('"Todas" marca la lista entera, no sólo la página visible', async () => {
    const { fixture, comp } = await muchasTablas(60);

    comp.page.next();
    await settle(fixture);
    comp.toggleAll(false);
    await settle(fixture);
    expect(comp.includedTables()).toEqual([]);

    comp.toggleAll(true);
    await settle(fixture);

    // Marcar 60 desde la página 2: es lo que dice el botón "Todas" del título.
    expect(comp.includedTables()).toHaveLength(60);
    expect(comp.page.total()).toBe(60);
  });

  it('cambiar de esquema vuelve a la primera página', async () => {
    const { fixture, comp } = await muchasTablas(60);

    comp.page.goTo(3);
    await settle(fixture);
    expect(comp.page.current()).toBe(3);

    // Las tablas del esquema nuevo son distintas: quedarse en la página 3 de
    // otra lista mostraría una grilla vacía sin explicación.
    tables = ['a', 'b'];
    comp.schema.set('otro');
    await settle(fixture);

    expect(comp.page.current()).toBe(1);
    expect(comp.page.total()).toBe(2);
  });

  it('con pocas tablas la barra no navega pero el tamaño sigue editable', async () => {
    const { el } = await ready();

    expect(el.querySelectorAll('table.filter-table tbody tr').length).toBe(3);
    // Con 3 tablas no hay nada que recorrer, pero el selector queda: una sola
    // "página" puede seguir desbordando la pantalla.
    expect(el.querySelector('.table-pagination__size select')).not.toBeNull();
    expect(el.querySelectorAll('.table-pagination button').length).toBe(0);
  });
});

describe('MigrateDataPage un solo día', () => {
  beforeEach(setup);

  it('el modo de un solo día escribe los dos límites con un solo campo', async () => {
    const { fixture, comp, el } = await ready();
    await marcarConRango(comp, fixture);

    comp.setMode('day');
    await settle(fixture);
    comp.day.set('2026-03-01');
    await settle(fixture);

    expect(el.querySelector('#migrate-data-day')).not.toBeNull();
    expect(el.querySelector('#migrate-data-from')).toBeNull();
    expect(comp.selection()[0]).toEqual({
      table: 'orders',
      date_column: 'created_at',
      date_from: '2026-03-01',
      date_to: '2026-03-01',
    });
  });

  it('un mes cubre el mes entero, del primero al último día', async () => {
    const { fixture, comp } = await ready();
    await marcarConRango(comp, fixture);

    comp.setMode('month');
    comp.month.set('2026-02');
    await settle(fixture);

    // Febrero de 2026: 28 días. El backend renderiza el tope exclusivo un día
    // después, así que mandar `2026-02-28` como `date_to` incluye todo el día 28.
    expect(comp.selection()[0].date_from).toBe('2026-02-01');
    expect(comp.selection()[0].date_to).toBe('2026-02-28');
  });

  it('un mes bisiesto llega al 29, no al 28', async () => {
    const { fixture, comp } = await ready();
    await marcarConRango(comp, fixture);

    comp.setMode('month');
    comp.month.set('2024-02');
    await settle(fixture);

    expect(comp.selection()[0].date_to).toBe('2024-02-29');
  });

  it('explica en la pantalla que los dos límites iguales son un solo día', async () => {
    const { fixture, comp, el } = await ready();
    await marcarConRango(comp, fixture, 'orders', '2026-03-01', '2026-03-01');
    comp.setMode('range');
    await settle(fixture);

    expect(comp.isSameDay({ from: '2026-03-01', to: '2026-03-01' })).toBe(true);
    expect(el.textContent).toContain('Se copia el día');
    expect(el.textContent).toContain('2026-03-01');
  });

  it('el rango ofrece los dos límites, y cada uno va en su input', async () => {
    const { fixture, comp, el } = await ready();
    await marcarConRango(comp, fixture);
    comp.setColumn('orders', 'created_at');
    await settle(fixture);

    // Se afirma sobre los dos inputs y sus labels, no sobre la prosa que
    // explicaba la inclusividad. Los labels dicen "inclusive" y el backend
    // renderiza el tope exclusivo un día después: eso es lo que hay que verificar.
    expect(el.querySelector('#migrate-data-from')).not.toBeNull();
    expect(el.querySelector('#migrate-data-to')).not.toBeNull();
    expect(el.querySelector('label[for="migrate-data-from"]')?.textContent).toContain('Desde');
    expect(el.querySelector('label[for="migrate-data-to"]')?.textContent).toContain('Hasta');
  });

  it('el filtro vive fuera de la tabla: un solo juego de controles para todas', async () => {
    const { fixture, comp, el } = await ready();
    comp.toggleAll(true);
    await settle(fixture);

    // Lo que se afirma es que hay UN juego de controles, no uno por fila: seis
    // tablas marcadas con seis juegos distintos era el problema.
    expect(el.querySelectorAll('input[name="migrate-data-mode"]').length).toBe(3);
    // El modo por defecto es mes, así que el campo que se asserta es el de mes.
    expect(el.querySelectorAll('#migrate-data-month')).toHaveLength(1);

    comp.setMode('range');
    await settle(fixture);
    expect(el.querySelectorAll('#migrate-data-from')).toHaveLength(1);
    expect(el.querySelectorAll('#migrate-data-to')).toHaveLength(1);

    // Y la tabla quedó con dos columnas: la tabla y su columna de fecha.
    expect(el.querySelectorAll('table.filter-table thead th').length).toBe(2);
  });

  it('el resumen de la ventana dice qué se va a copiar', async () => {
    const { fixture, comp, el } = await ready();
    comp.toggle('orders', true);
    await settle(fixture);
    comp.dateMode.set('month');
    comp.month.set('2026-03');
    await settle(fixture);

    // El texto traduce el modo a la ventana concreta: "un mes" no es un período
    // que se pueda leer de un vistazo sin saber si es marzo o todo marzo.
    expect(el.textContent).toContain('2026-03-01');
    expect(el.textContent).toContain('2026-03-31');
  });

  it('un rango sin ningún límite cargado no es enviable', async () => {
    const { fixture, comp } = await ready();
    comp.dateMode.set('range');
    comp.toggle('orders', true);
    await settle(fixture);

    // Modo elegido y cero fechas es "todavía no está decidido", no "sin filtro".
    expect(comp.window()).toBeNull();
    expect(comp.filterlessTables()).toEqual(['orders']);
    expect(comp.canSubmit()).toBe(false);
  });

  it('todas las tablas marcadas reciben la misma ventana', async () => {
    const { fixture, comp } = await ready();
    comp.toggleAll(true);
    await settle(fixture);
    comp.dateMode.set('month');
    comp.month.set('2026-03');
    await settle(fixture);

    const conColumna = comp.selection().filter((t: any) => t.date_column);
    expect(conColumna.length).toBe(2); // `config` no tiene columnas de fecha
    for (const t of conColumna) {
      expect(t.date_from).toBe('2026-03-01');
      expect(t.date_to).toBe('2026-03-31');
    }
  });

  it('la columna sigue siendo por tabla, la ventana no', async () => {
    const { fixture, comp } = await ready();
    comp.toggleAll(true);
    await settle(fixture);
    comp.dateMode.set('month');
    comp.month.set('2026-03');

    // Cada tabla puede filtrar por su propia columna: eso no se unificó.
    comp.setColumn('orders', 'updated_at');
    await settle(fixture);

    const byTable = Object.fromEntries(
      comp.selection().map((t: any) => [t.table, t.date_column]),
    );
    expect(byTable).toEqual({ orders: 'updated_at', lines: 'created_at', config: null });
  });

  it('cambiar de rango a mes conserva la ventana', async () => {
    const { fixture, comp } = await ready();
    await marcarConRango(comp, fixture);

    comp.setMode('month');
    await settle(fixture);

    // 1–31 de marzo sigue siendo marzo. Perder la ventana porque el usuario
    // quiso ver un mes en vez de un rango sería un retroceso sin motivo.
    expect(comp.window()).toEqual({ from: '2026-03-01', to: '2026-03-31' });
  });

  it('cambiar de rango a día toma el primer día de la ventana', async () => {
    const { fixture, comp } = await ready();
    await marcarConRango(comp, fixture);

    comp.setMode('day');
    await settle(fixture);

    expect(comp.day()).toBe('2026-03-01');
    expect(comp.window()).toEqual({ from: '2026-03-01', to: '2026-03-01' });
  });

  it('un rango que se abrió en un día vuelve como ese día, no como el mes entero', async () => {
    const { fixture, comp } = await ready();
    await marcarConRango(comp, fixture);
    comp.setMode('day');
    await settle(fixture);

    comp.setMode('range');
    await settle(fixture);

    // El modo `day` ya había reducido la ventana a un día, así que volver al
    // rango devuelve ese día y no el mes que tenía antes: se conserva *la
    // ventana que se estaba mirando*, no un histórico de cada campo escrito.
    expect(comp.window()).toEqual({ from: '2026-03-01', to: '2026-03-01' });
  });

  it('un rango al revés no es enviable y lo dice', async () => {
    const { fixture, comp } = await ready();
    await marcarConRango(comp, fixture);
    comp.setMode('range');
    comp.dateFrom.set('2026-03-31');
    comp.dateTo.set('2026-03-01');
    await settle(fixture);

    // Renderizada tal cual sería una ventana que nunca matchea nada, y "0 filas
    // migradas" se lee como "no había datos" en vez de "las fechas están al revés".
    expect(comp.window()).toBeNull();
    expect(comp.canSubmit()).toBe(false);
    expect(comp.submitHint()).toContain('posterior a la final');
  });
});

describe('MigrateDataPage simular y migrar', () => {
  beforeEach(async () => {
    await setup();
    // El espía va siempre: `Simular` no debe preguntar, y eso sólo se prueba si
    // `window.confirm` está vigilado.
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  afterEach(() => vi.restoreAllMocks());

  it('Simular manda dry_run true y no manda confirm true', async () => {
    const { fixture, comp } = await ready();
    await marcarConRango(comp, fixture);

    comp.simulate();
    await settle(fixture);

    expect(sent).toHaveLength(1);
    expect(sent[0].dry_run).toBe(true);
    // `confirm` va explícito en false, como en "Migrar info": una simulación no
    // puede pedir permiso para escribir.
    expect(sent[0].confirm).toBe(false);
    // Simular no escribe: no puede pedir confirmación.
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it('Migrar manda confirm true', async () => {
    const { fixture, comp } = await ready();
    await marcarConRango(comp, fixture);

    comp.migrate();
    await settle(fixture);

    expect(sent).toHaveLength(1);
    expect(sent[0].dry_run).toBe(false);
    expect(sent[0].confirm).toBe(true);
  });

  it('Migrar pregunta antes y el aviso dice que escribe y reemplaza por primary key', async () => {
    const { fixture, comp } = await ready();
    await marcarConRango(comp, fixture);

    comp.migrate();
    await settle(fixture);

    const mensaje = vi.mocked(window.confirm).mock.calls[0][0];
    expect(mensaje).toContain('¿Migrar 1 tabla(s) de yappy de dev a local?');
    expect(mensaje).toContain('escribe filas en local');
    expect(mensaje).toContain('primary key');
  });

  it('declinar la confirmación no manda nada', async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    const { fixture, comp } = await ready();
    await marcarConRango(comp, fixture);

    comp.migrate();
    await settle(fixture);

    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(sent).toHaveLength(0);
    expect(comp.result()).toBeNull();
    expect(comp.busy()).toBe(false);
  });

  it('manda exactamente las tablas marcadas con sus filtros', async () => {
    const { fixture, comp } = await ready();

    // `lines` marcada con filtro, `config` marcada entera, `orders` sin marcar.
    comp.dateMode.set('range');
    comp.dateFrom.set('2026-03-01');
    comp.dateTo.set('2026-03-31');
    comp.toggle('lines', true);
    comp.toggle('config', true);
    await settle(fixture);
    comp.setColumn('lines', 'created_at');
    await settle(fixture);

    comp.simulate();
    await settle(fixture);

    expect(sent[0]).toEqual({
      env_b: 'dev',
      env_a: 'local',
      schema_name: 'yappy',
      tables: [
        {
          table: 'lines',
          date_column: 'created_at',
          date_from: '2026-03-01',
          date_to: '2026-03-31',
        },
        { table: 'config', date_column: null, date_from: null, date_to: null },
      ],
      dry_run: true,
      confirm: false,
    });
    // `orders` quedó sin marcar: no viaja.
    expect(sent[0].tables.map((t) => t.table)).not.toContain('orders');
  });

  it('permite una sola de las dos fechas', async () => {
    const { fixture, comp } = await ready();
    comp.dateMode.set('range');
    comp.dateFrom.set('2026-03-01');
    comp.toggle('lines', true);
    await settle(fixture);

    comp.simulate();
    await settle(fixture);

    expect(sent[0].tables[0]).toEqual({
      table: 'lines',
      date_column: 'created_at',
      date_from: '2026-03-01',
      date_to: null,
    });
  });

  it('la tabla sin columnas de fecha se manda sin filtro', async () => {
    const { fixture, comp } = await ready();
    comp.toggle('config', true);
    await settle(fixture);

    comp.simulate();
    await settle(fixture);

    expect(sent[0].tables).toEqual([
      { table: 'config', date_column: null, date_from: null, date_to: null },
    ]);
  });

  it('con el esquema vacío no se listan tablas y no se puede enviar', async () => {
    const fixture = TestBed.createComponent(MigrateDataPage);
    const comp = fixture.componentInstance as any;
    const el = fixture.nativeElement as HTMLElement;
    await settle(fixture);

    expect(comp.canSubmit()).toBe(false);
    expect(el.querySelector('#migrate-table-orders')).toBeNull();
    expect(el.textContent).toContain('Elegí el ambiente de origen y el de destino.');
    expect(button(el, 'Simular').disabled).toBe(true);
    expect(button(el, 'Migrar a').disabled).toBe(true);
  });

  it('sin tablas marcadas no se puede enviar', async () => {
    const { fixture, comp, el } = await ready();
    expect(el.querySelector('#migrate-table-orders')).not.toBeNull();

    expect(comp.selection()).toHaveLength(0);
    expect(comp.canSubmit()).toBe(false);
    expect(el.textContent).toContain('Marcá al menos una tabla para migrar.');
    expect(button(el, 'Simular').disabled).toBe(true);
    expect(button(el, 'Migrar a').disabled).toBe(true);

    comp.simulate();
    await settle(fixture);

    expect(sent).toHaveLength(0);
  });

  it('con origen y destino iguales no se puede enviar', async () => {
    const { fixture, comp, el } = await ready();
    comp.envA.set('dev');
    comp.toggle('orders', true);
    await settle(fixture);

    expect(comp.sameEnv()).toBe(true);
    expect(comp.canSubmit()).toBe(false);
    expect(button(el, 'Migrar a').disabled).toBe(true);
  });

  it('un resultado viejo se descarta cuando cambia la selección', async () => {
    const { fixture, comp } = await ready();
    await marcarConRango(comp, fixture);
    comp.simulate();
    await settle(fixture);
    expect(comp.result()).not.toBeNull();

    comp.toggle('config', true);
    await settle(fixture);

    expect(comp.result()).toBeNull();
  });
});

describe('MigrateDataPage resultado', () => {
  beforeEach(async () => {
    await setup();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  afterEach(() => vi.restoreAllMocks());

  it('renderiza cada tabla con su estado, sus conteos y el SELECT usado', async () => {
    const { fixture, comp, el } = await ready();
    await marcarConRango(comp, fixture);

    comp.migrate();
    await settle(fixture);

    expect(el.textContent).toContain('Resultado por tabla');
    expect(el.textContent).toContain('yappy.orders');
    expect(el.textContent).toContain('120 leída(s)');
    expect(el.textContent).toContain('118 reemplazada(s)');
    expect(el.textContent).toContain('La tabla yappy.config no existe en el destino');
    const pres = [...el.querySelectorAll('pre.stmt-preview')].map((p) => p.textContent ?? '');
    expect(pres.some((t) => t.includes('SELECT * FROM `yappy`.`orders`'))).toBe(true);
    expect(pres.some((t) => t.includes("`created_at` >= '2026-03-01 00:00:00'"))).toBe(true);
  });

  it('lista las columnas omitidas del destino y el resumen de la migración', async () => {
    const { fixture, comp, el } = await ready();
    comp.toggle('config', true);
    await settle(fixture);

    comp.migrate();
    await settle(fixture);

    expect(el.textContent).toContain('columnas que no existen en el destino, omitidas');
    expect(el.textContent).toContain('legacy_id, note');
    expect(el.textContent).toContain('1 OK, 1 error(es)');
  });

  it('muestra todas las notas enteras, sin recortarlas', async () => {
    const { fixture, comp, el } = await ready();
    await marcarConRango(comp, fixture);

    comp.migrate();
    await settle(fixture);

    for (const n of BASE['notes'] as string[]) {
      expect(el.textContent).toContain(n);
    }
  });

  it('no deja enviar una tabla con columna de fecha elegida pero sin fechas', async () => {
    const { fixture, comp, el } = await ready();
    comp.toggle('orders', true);
    await settle(fixture);

    // Marcar una tabla le pone por defecto la primera columna de fecha. Sin
    // ninguna fecha el filtro NO acota: el SELECT sale sin WHERE y se copia la
    // tabla entera. Nadie espera eso de un formulario con "Rango" elegido y dos
    // campos vacíos, y es la forma de vaciar una tabla de producción en dev.
    expect(comp.filters().orders.column).toBe('created_at');
    expect(comp.filterlessTables()).toEqual(['orders']);
    expect(comp.canSubmit()).toBe(false);
    expect(comp.submitHint()).toContain('se copiarían enteras');
    expect(button(el, 'Simular').disabled).toBe(true);

    comp.simulate();
    await settle(fixture);
    expect(sent).toHaveLength(0);
  });

  it('con un solo límite ya se puede enviar: "desde marzo en adelante"', async () => {
    const { fixture, comp } = await ready();
    comp.dateMode.set('range');
    comp.dateFrom.set('2026-03-01');
    comp.toggle('orders', true);
    await settle(fixture);

    // Un rango abierto por un lado es una migración como cualquier otra: no
    // entra en `filterlessTables` porque hay una fecha.
    expect(comp.filterlessTables()).toEqual([]);
    expect(comp.canSubmit()).toBe(true);
  });

  it('la forma honesta de migrarla entera es "Todo (sin filtro)"', async () => {
    const { fixture, comp } = await ready();
    comp.toggle('orders', true);
    await settle(fixture);
    expect(comp.canSubmit()).toBe(false);

    comp.setColumn('orders', '');
    await settle(fixture);

    expect(comp.filterlessTables()).toEqual([]);
    expect(comp.canSubmit()).toBe(true);
    expect(comp.selection()[0].date_column).toBeNull();
  });

  it('la simulación se marca como simulación', async () => {
    migrateResponse = { ...BASE, dry_run: true };
    const { fixture, comp, el } = await ready();
    await marcarConRango(comp, fixture);

    comp.simulate();
    await settle(fixture);

    expect(el.textContent).toContain('Simulación');
    expect(el.textContent).toContain('Filas por tabla');
    expect(el.textContent).not.toContain('Resultado por tabla');
  });
});

describe('MigrateDataPage respuestas fuera de orden', () => {
  beforeEach(setup);

  it('descarta la respuesta de un esquema que el usuario ya dejó', async () => {
    // Las dos llamadas quedan pendientes hasta que el test las resuelve, y se
    // resuelven al revés: la de `yappy` llega después de la de `otro`.
    const pending: Array<{ schema: string; resolve: (columns: DateColumnInfo[]) => void }> = [];
    dateColumnsFor = (env, schema, table) =>
      new Promise<DateColumnsResponse>((resolve) => {
        pending.push({
          schema,
          resolve: (columns) => resolve({ env, schema_name: schema, table_name: table, columns }),
        });
      });

    const fixture = TestBed.createComponent(MigrateDataPage);
    const comp = fixture.componentInstance as any;
    comp.envB.set('dev');
    await settle(fixture);
    comp.schema.set('yappy');
    await settle(fixture);

    comp.toggle('orders', true);
    await settle(fixture);
    expect(pending).toHaveLength(1);

    // Cambiar de esquema deja la respuesta de `yappy` en el aire.
    comp.schema.set('otro');
    await settle(fixture);
    comp.toggle('orders', true);
    await settle(fixture);
    expect(pending).toHaveLength(2);

    pending[1].resolve([{ name: 'created_at', type: 'datetime' }]);
    await settle(fixture);
    pending[0].resolve([{ name: 'archivada_en', type: 'timestamp' }]);
    await settle(fixture);

    // La de `yappy` llegó última, pero no es del esquema que está en pantalla.
    expect(comp.filters().orders.column).toBe('created_at');
    expect(comp.filters().orders.columns?.map((c: DateColumnInfo) => c.name)).toEqual([
      'created_at',
    ]);
  });
});