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
      ...DATE_COLUMNS[table],
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

    expect(el.textContent).toContain('Sin columnas de fecha: se migra completa');
    expect(el.querySelector('#migrate-date-column-config')).toBeNull();
    expect(el.querySelector('#migrate-from-config')).toBeNull();
    expect(el.querySelector('#migrate-to-config')).toBeNull();
    expect(comp.filters().config.column).toBe('');
  });

  it('no deja escribir un nombre de columna que no sea de la tabla', async () => {
    const { fixture, comp, el } = await ready();
    comp.toggle('orders', true);
    await settle(fixture);

    comp.setColumn('orders', 'usuario_id');
    await settle(fixture);

    expect(comp.filters().orders.column).toBe('');
    expect(comp.selection()[0].date_column).toBeNull();
    expect(el.textContent).toContain('Sin columna de filtro');
  });

  it('una columna de fecha elegida deja habilitados los dos campos de fecha', async () => {
    const { fixture, comp, el } = await ready();
    comp.toggle('orders', true);
    await settle(fixture);

    expect(el.querySelector('#migrate-from-orders')?.hasAttribute('disabled')).toBeFalsy();
    expect(el.querySelector('#migrate-to-orders')?.hasAttribute('disabled')).toBeFalsy();
  });

  it('con "Todo" no muestra los campos de fecha y limpia los que había', async () => {
    const { fixture, comp, el } = await ready();
    comp.toggle('orders', true);
    await settle(fixture);
    comp.setDay('orders', '2026-03-01');
    await settle(fixture);

    comp.setColumn('orders', '');
    await settle(fixture);

    expect(el.querySelector('#migrate-from-orders')).toBeNull();
    expect(comp.filters().orders.dateFrom).toBe('');
    expect(comp.selection()[0]).toEqual({
      table: 'orders',
      date_column: null,
      date_from: null,
      date_to: null,
    });
  });

  it('los campos de fecha muestran los límites cargados', async () => {
    const { fixture, comp, el } = await ready();
    comp.toggle('orders', true);
    await settle(fixture);
    comp.setFrom('orders', '2026-03-01');
    comp.setTo('orders', '2026-03-31');
    await settle(fixture);

    expect((el.querySelector('#migrate-from-orders') as HTMLInputElement).value).toBe('2026-03-01');
    expect((el.querySelector('#migrate-to-orders') as HTMLInputElement).value).toBe('2026-03-31');
  });

  it('desmarcar y remarkar no vuelve a preguntar las columnas', async () => {
    const { fixture, comp } = await ready();
    comp.toggle('orders', true);
    await settle(fixture);
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

    expect(comp.includedTables()).toEqual(TABLES);
    expect(dateColumnCalls.map((c) => c.table)).toEqual(TABLES);
    expect(comp.canSubmit()).toBe(true);
  });

  it('no deja enviar una tabla cuyas columnas de fecha no se pudieron leer', async () => {
    dateColumnsFor = () => Promise.reject(new Error('boom'));
    const { fixture, comp, el } = await ready();

    comp.toggle('orders', true);
    await settle(fixture);

    expect(el.textContent).toContain('No se pudieron leer las columnas de fecha de orders');
    // Mandarla sin filtro la copiaría entera: eso se frena acá, no en el backend.
    expect(comp.canSubmit()).toBe(false);
    expect(comp.submitHint()).toContain('columnas de fecha');
    expect(button(el, 'Migrar a').disabled).toBe(true);

    comp.simulate();
    await settle(fixture);

    expect(sent).toHaveLength(0);
  });
});

describe('MigrateDataPage un solo día', () => {
  beforeEach(setup);

  it('el modo de un solo día escribe los dos límites con un solo campo', async () => {
    const { fixture, comp, el } = await ready();
    comp.toggle('orders', true);
    await settle(fixture);

    comp.setSingleDay('orders', true);
    await settle(fixture);
    comp.setDay('orders', '2026-03-01');
    await settle(fixture);

    expect(el.querySelector('#migrate-day-orders')).not.toBeNull();
    expect(el.querySelector('#migrate-from-orders')).toBeNull();
    expect(comp.selection()[0]).toEqual({
      table: 'orders',
      date_column: 'created_at',
      date_from: '2026-03-01',
      date_to: '2026-03-01',
    });
  });

  it('explica en la pantalla que los dos límites iguales son un solo día', async () => {
    const { fixture, comp, el } = await ready();
    comp.toggle('orders', true);
    await settle(fixture);
    comp.setFrom('orders', '2026-03-01');
    comp.setTo('orders', '2026-03-01');
    await settle(fixture);

    expect(comp.isSameDay(comp.filters().orders)).toBe(true);
    expect(el.textContent).toContain('Un solo día: 2026-03-01');
    expect(el.textContent).toContain('la ventana es ese día entero');
  });

  it('el rango ofrece los dos límites, y cada uno va en su input', async () => {
    const { fixture, comp, el } = await ready();
    comp.toggle('orders', true);
    await settle(fixture);
    comp.setColumn('orders', 'created_at');
    await settle(fixture);

    // Se afirma sobre los dos inputs y sus labels, no sobre la prosa que
    // explicaba la inclusividad. Los labels dicen "inclusive" y el backend
    // renderiza el tope exclusivo un día después: eso es lo que hay que verificar.
    expect(el.querySelector('#migrate-from-orders')).not.toBeNull();
    expect(el.querySelector('#migrate-to-orders')).not.toBeNull();
    expect(el.querySelector('label[for="migrate-from-orders"]')?.textContent).toContain(
      'Desde',
    );
    expect(el.querySelector('label[for="migrate-to-orders"]')?.textContent).toContain('Hasta');
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
    comp.toggle('orders', true);
    await settle(fixture);

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
    comp.toggle('orders', true);
    await settle(fixture);

    comp.migrate();
    await settle(fixture);

    expect(sent).toHaveLength(1);
    expect(sent[0].dry_run).toBe(false);
    expect(sent[0].confirm).toBe(true);
  });

  it('Migrar pregunta antes y el aviso dice que escribe y reemplaza por primary key', async () => {
    const { fixture, comp } = await ready();
    comp.toggle('orders', true);
    await settle(fixture);

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
    comp.toggle('orders', true);
    await settle(fixture);

    comp.migrate();
    await settle(fixture);

    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(sent).toHaveLength(0);
    expect(comp.result()).toBeNull();
    expect(comp.busy()).toBe(false);
  });

  it('manda exactamente las tablas marcadas con sus filtros', async () => {
    const { fixture, comp } = await ready();

    // `lines` marcada con un rango, `config` marcada entera, `orders` sin marcar.
    comp.toggle('lines', true);
    comp.toggle('config', true);
    await settle(fixture);
    comp.setColumn('lines', 'created_at');
    comp.setFrom('lines', '2026-03-01');
    comp.setTo('lines', '2026-03-31');
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
    comp.toggle('lines', true);
    await settle(fixture);
    comp.setFrom('lines', '2026-03-01');
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
    comp.toggle('orders', true);
    await settle(fixture);
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
    comp.toggle('orders', true);
    await settle(fixture);

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
    comp.toggle('orders', true);
    await settle(fixture);

    comp.migrate();
    await settle(fixture);

    for (const n of BASE['notes'] as string[]) {
      expect(el.textContent).toContain(n);
    }
  });

  it('la simulación se marca como simulación', async () => {
    migrateResponse = { ...BASE, dry_run: true };
    const { fixture, comp, el } = await ready();
    comp.toggle('orders', true);
    await settle(fixture);

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