/**
 * Red de seguridad mínima de la página de SQL, escrita **antes** de restilarla.
 *
 * `sql.ts` era la única de las cuatro secciones de base sin un solo test, y el
 * rediseño la movía entero: si algo se rompía, nadie se enteraba hasta que lo
 * tocaba un usuario. Estos tests no son exhaustivos —cubin el andamiaje y los
 * caminos que el template puede romper por un refactor visual— y se escribieron
 * contra el componente **sin tocar**, para que la migración no empiece con la
 * prueba en rojo.
 *
 * Regla que aplican: afirmar sobre el mecanismo (qué se manda al servicio, qué
 * componente se renderiza), nunca sobre el texto de una leyenda. Un assert de
 * caption se rompe cada vez que se limpia una línea de copy.
 */
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { SqlPage } from './sql';
import { DbService } from '../core/services/db.service';
import { EnvironmentService } from '../core/services/environment.service';
import { MigrationRequest, QueryRequest } from '../api-gen/models';

const QUERY_OK: Record<string, any> = {
  env: 'dev',
  columns: ['pedido_id', 'total'],
  rows: [
    { pedido_id: 1, total: '100.00' },
    { pedido_id: 2, total: '250.50' },
  ],
  total: 2,
  truncated: false,
  ms: 12,
};

/** Un SELECT con `n` filas, para probar la paginación de la grilla. */
function queryWithRows(n: number): Record<string, any> {
  return {
    ...QUERY_OK,
    rows: Array.from({ length: n }, (_, i) => ({ pedido_id: i + 1, total: `${i}.50` })),
    total: n,
  };
}

const MIGRATION_OK: Record<string, any> = {
  env_b: 'dev',
  env_a: 'local',
  dry_run: false,
  tables: [
    {
      schema_name: 'yappy',
      table_name: 'pedidos',
      alias: 'p',
      target_schema: 'yappy',
      target_table: 'pedidos',
      select_sql: 'SELECT DISTINCT `p`.* FROM ...',
      row_count: 2,
      replaced: 2,
      skipped_columns: [],
      ok: true,
      error: null,
    },
  ],
  notes: ['Se migran 1 tabla(s).'],
  ok_count: 1,
  err_count: 0,
};

describe('SqlPage', () => {
  let sentQueries: QueryRequest[];
  let sentMigrations: MigrationRequest[];
  let mockQuery: () => Promise<Record<string, any>>;
  let mockMigration: () => Promise<Record<string, any>>;

  beforeEach(async () => {
    sentQueries = [];
    sentMigrations = [];
    mockQuery = () => Promise.resolve(QUERY_OK);
    mockMigration = () => Promise.resolve(MIGRATION_OK);

    await TestBed.configureTestingModule({
      imports: [SqlPage],
      providers: [
        provideRouter([]),
        {
          provide: EnvironmentService,
          useValue: { list: () => Promise.resolve({ environments: [] }) },
        },
        {
          provide: DbService,
          useValue: {
            query: (req: QueryRequest) => {
              sentQueries.push(req);
              return mockQuery();
            },
            migrate: (req: MigrationRequest) => {
              sentMigrations.push(req);
              return mockMigration();
            },
          },
        },
      ],
    }).compileComponents();
  });

  async function settle(fixture: ComponentFixture<SqlPage>): Promise<void> {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  }

  async function consultar(): Promise<{ comp: any; el: HTMLElement }> {
    const fixture = TestBed.createComponent(SqlPage);
    const comp = fixture.componentInstance as any;
    comp.envs.set(['dev']);
    comp.sql.set('SELECT * FROM `yappy`.`pedidos`');
    comp.runQuery();
    await settle(fixture);
    return { comp, el: fixture.nativeElement as HTMLElement };
  }

  it('manda la consulta del editor con el ambiente elegido', async () => {
    const { comp } = await consultar();

    expect(sentQueries).toHaveLength(1);
    expect(sentQueries[0].env).toBe('dev');
    expect(sentQueries[0].code).toBe('SELECT * FROM `yappy`.`pedidos`');
    expect(comp.result()).not.toBeNull();
  });

  it('pinta el resultado como tabla, una columna por header', async () => {
    const { el } = await consultar();

    const headers = [...el.querySelectorAll('table.data-table thead th')].map((th) =>
      th.textContent?.trim(),
    );
    expect(headers).toEqual(['pedido_id', 'total']);

    const firstRow = [...el.querySelectorAll('table.data-table tbody tr')][0];
    expect([...firstRow.querySelectorAll('td')].map((td) => td.textContent?.trim())).toEqual([
      '1',
      '100.00',
    ]);
  });

  it('solo ofrece Migrar info cuando la consulta devolvió filas', async () => {
    mockQuery = () => Promise.resolve({ ...QUERY_OK, rows: [], total: 0 });
    const { comp, el } = await consultar();

    // Se afirma sobre `showMigrate`, no sobre el texto "Migrar info": esa
    // palabra también está en la intro de la página, así que un assert de
    // caption no distinguiría "no hay panel" de "la intro lo menciona".
    expect(comp.showMigrate()).toBe(false);
    expect(el.querySelector('.section-title')).toBeNull();
  });

  it('muestra Migrar info con filas, y la simulación no escribe', async () => {
    const fixture = TestBed.createComponent(SqlPage);
    const comp = fixture.componentInstance as any;
    comp.envs.set(['dev']);
    comp.sql.set('SELECT * FROM `yappy`.`pedidos`');
    comp.runQuery();
    await settle(fixture);

    expect(comp.showMigrate()).toBe(true);

    comp.destEnvs.set(['local']);
    await settle(fixture);
    comp.previewMigration();
    await settle(fixture);

    expect(sentMigrations).toHaveLength(1);
    expect(sentMigrations[0].dry_run).toBe(true);
    expect(sentMigrations[0].confirm).toBe(false);
    expect(sentMigrations[0].env_b).toBe('dev');
    expect(sentMigrations[0].env_a).toBe('local');
  });

  it('la migración real exige la casilla de confirmación y manda confirm', async () => {
    const fixture = TestBed.createComponent(SqlPage);
    const comp = fixture.componentInstance as any;
    comp.envs.set(['dev']);
    comp.sql.set('SELECT * FROM `yappy`.`pedidos`');
    comp.runQuery();
    await settle(fixture);

    comp.destEnvs.set(['local']);
    await settle(fixture);

    // Sin la casilla, no puede migrar: el botón está deshabilitado.
    expect(comp.canMigrate()).toBe(false);
    comp.runMigration();
    await settle(fixture);
    expect(sentMigrations).toHaveLength(0);

    comp.confirmChecked.set(true);
    await settle(fixture);
    comp.runMigration();
    await settle(fixture);

    expect(sentMigrations).toHaveLength(1);
    expect(sentMigrations[0].dry_run).toBe(false);
    expect(sentMigrations[0].confirm).toBe(true);
  });

  it('la casilla es un gate: sin destino no habilita migrar', async () => {
    const fixture = TestBed.createComponent(SqlPage);
    const comp = fixture.componentInstance as any;
    comp.envs.set(['dev']);
    comp.sql.set('SELECT * FROM `yappy`.`pedidos`');
    comp.runQuery();
    await settle(fixture);

    comp.confirmChecked.set(true);
    await settle(fixture);

    expect(comp.destEnv()).toBe('');
    expect(comp.canMigrate()).toBe(false);
  });

  // --- la grilla se pagina ---------------------------------------------------
  //
  // El backend trae hasta 500 filas de una vez. Antes de la paginación el
  // `@for` era sobre `rows()` y la tabla volcaba las 500: tres pantallas de
  // scroll para llegar a la última fila.

  async function consultarCon(query: Record<string, any>) {
    mockQuery = () => Promise.resolve(query);
    const fixture = TestBed.createComponent(SqlPage);
    const comp = fixture.componentInstance as any;
    comp.envs.set(['dev']);
    comp.sql.set('SELECT * FROM `yappy`.`pedidos`');
    comp.runQuery();
    await settle(fixture);
    return { comp, fixture, el: fixture.nativeElement as HTMLElement };
  }

  it('muestra sólo una página de filas y el resto queda en la barra', async () => {
    const { el } = await consultarCon(queryWithRows(60));

    expect(el.querySelectorAll('table.data-table tbody tr').length).toBe(25);
    expect(el.querySelector('.table-pagination')?.textContent).toContain('1–25 de 60');
  });

  it('avanzar muestra la segunda página de verdad', async () => {
    const { comp, fixture } = await consultarCon(queryWithRows(60));

    const botones = (fixture.nativeElement as HTMLElement).querySelectorAll(
      '.table-pagination button',
    );
    (botones[1] as HTMLButtonElement).click();
    await settle(fixture);

    expect(comp.page.current()).toBe(2);
    expect(comp.page.visible()[0]).toEqual({ pedido_id: 26, total: '25.50' });
    const primeraFila = (fixture.nativeElement as HTMLElement).querySelector(
      'table.data-table tbody tr td',
    );
    expect(primeraFila?.textContent?.trim()).toBe('26');
  });

  it('con menos de una página la barra no navega pero el tamaño sigue editable', async () => {
    const { el } = await consultarCon(queryWithRows(3));

    expect(el.querySelectorAll('table.data-table tbody tr').length).toBe(3);
    // Con 3 filas no hay nada que recorrer, pero el selector queda: una sola
    // "página" puede seguir desbordando la pantalla.
    expect(el.querySelector('.table-pagination__size select')).not.toBeNull();
    expect(el.querySelectorAll('.table-pagination button').length).toBe(0);
  });

  it('poblar no depende de cuántas filas se ven: "Migrar info" sigue ahí', async () => {
    const { comp, el } = await consultarCon(queryWithRows(60));

    // El bug de fondo: si `showMigrate` leyera la grilla paginada, con 500 filas
    // el botón desaparecería después de la primera página.
    expect(comp.rows().length).toBe(60);
    expect(comp.showMigrate()).toBe(true);
    expect(comp.page.total()).toBe(60);
    expect(el.textContent).toContain('Migrar info');
  });

  it('una consulta nueva arranca en la primera página', async () => {
    const { comp, fixture } = await consultarCon(queryWithRows(60));

    comp.page.next();
    await settle(fixture);
    expect(comp.page.current()).toBe(2);

    // La página 2 describía el resultado anterior.
    comp.runQuery();
    await settle(fixture);

    expect(comp.page.current()).toBe(1);
  });

  // --- stored procedures --------------------------------------------------
  //
  // `CALL` no es un SELECT: es lo único que esta sección corre que puede
  // escribir. Antes el backend lo rechazaba y la página no podía llamar un
  // procedure; ahora entra, y lo que se verifica es que entra *por esa vía*
  // y que el resto de las escrituras siguen sin entrar.

  it('la página ofrece llamar un stored procedure', async () => {
    const fixture = TestBed.createComponent(SqlPage);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    // El aviso de que un procedure puede escribir va en la intro, no escondido:
    // es la diferencia entre "consultar" y "correr algo que puede escribir".
    expect(el.textContent).toContain('stored procedure');
    expect(el.textContent).toContain('puede escribir');
  });

  it('un CALL se manda al servicio como cualquier otra consulta', async () => {
    const fixture = TestBed.createComponent(SqlPage);
    const comp = fixture.componentInstance as any;
    comp.envs.set(['dev']);
    comp.sql.set("CALL sp_migrar_pagos('2026-03-01')");
    comp.runQuery();
    await settle(fixture);

    expect(sentQueries[0].code).toBe("CALL sp_migrar_pagos('2026-03-01')");
    expect(comp.error()).toBeNull();
  });

  // --- el error va a un modal, no a la línea donde ocurrió -------------------
  //
  // El mensaje que forzó esto es el del túnel muerto: ~250 caracteres que dicen
  // que el puerto está abierto pero MySQL no respondió, qué revisar y dónde está
  // el log. Eso es un párrafo, y un párrafo en `.error-box` empuja toda la
  // página — la misma regla de `docs/lenguaje-visual.md` que motivó
  // `app-notice-modal` en la página de migración.

  const TUNNEL_ERROR =
    'El túnel SSM no respondió en localhost:8101: el puerto está abierto pero no llegó el ' +
    'saludo de MySQL. Suele significar que la instancia no puede alcanzar el host remoto.';

  async function consultarConError(message: string) {
    mockQuery = () => Promise.reject(new Error(message));
    const fixture = TestBed.createComponent(SqlPage);
    const comp = fixture.componentInstance as any;
    comp.envs.set(['dev']);
    comp.sql.set('SELECT * FROM `yappy`.`pedidos`');
    comp.runQuery();
    await settle(fixture);
    return { comp, fixture, el: fixture.nativeElement as HTMLElement };
  }

  it('un error largo se lee en un modal, no en un error-box en la página', async () => {
    const { el } = await consultarConError(TUNNEL_ERROR);

    expect(el.querySelector('.notice-modal')).not.toBeNull();
    expect(el.querySelector('.notice-modal')?.textContent).toContain('localhost:8101');
    // El párrafo completo no vive en la línea donde ocurrió.
    expect(el.querySelector('.error-box')).toBeNull();
    expect(el.querySelector('.error-box')?.textContent ?? '').not.toContain('8101');
  });

  it('el marcador que queda en la línea es corto y reabre el modal', async () => {
    const { comp, fixture, el } = await consultarConError(TUNNEL_ERROR);

    comp.dismissNotice();
    await settle(fixture);
    expect(el.querySelector('.notice-modal')).toBeNull();

    const marker = el.querySelector('.error-marker') as HTMLButtonElement;
    expect(marker).not.toBeNull();
    expect(marker.textContent?.trim().length ?? 0).toBeLessThan(60);
    expect(marker.textContent).not.toContain('8101');

    marker.click();
    await settle(fixture);

    expect(comp.noticeOpen()).toBe(true);
    expect(el.querySelector('.notice-modal')).not.toBeNull();
  });

  it('cerrar el aviso no borra el error: el marcador sigue ahí', async () => {
    // El modal es sólo presentación. El texto sigue en `error`, así que siempre
    // se puede volver a abrir.
    const { comp, fixture, el } = await consultarConError(TUNNEL_ERROR);

    comp.dismissNotice();
    await settle(fixture);

    expect(comp.error()).toContain('localhost:8101');
    expect(el.querySelector('.error-marker')).not.toBeNull();
  });

  it('cerrar el aviso pega: el change detection no lo reabre', async () => {
    // El motivo de los dos contadores. Sin ellos, `noticeOpen` volvería a
    // `true` en el próximo ciclo porque el error sigue en el signal.
    const { comp, fixture, el } = await consultarConError(TUNNEL_ERROR);
    expect(el.querySelector('.notice-modal')).not.toBeNull();

    comp.dismissNotice();
    await settle(fixture);

    expect(comp.noticeOpen()).toBe(false);
    expect(el.querySelector('.notice-modal')).toBeNull();

    // Más ciclos sin nada nuevo: sigue cerrado.
    fixture.detectChanges();
    await settle(fixture);
    expect(comp.noticeOpen()).toBe(false);
  });

  it('un fallo nuevo sí reabre el modal', async () => {
    // Reintentar la misma consulta es un fallo nuevo, y tiene que volver a
    // avisar: si no, el modal se cerraría para siempre ante un error que el
    // usuario puede reproducir.
    const { comp, fixture } = await consultarConError(TUNNEL_ERROR);

    comp.dismissNotice();
    await settle(fixture);
    expect(comp.noticeOpen()).toBe(false);

    comp.runQuery();
    await settle(fixture);

    expect(comp.noticeOpen()).toBe(true);
    expect(comp.error()).toContain('localhost:8101');
  });

  it('el editor de la consulta sigue siendo un textarea#sql', () => {
    const fixture = TestBed.createComponent(SqlPage);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    // El id lo consulta el resto del flujo; el refactor visual no lo renombra.
    expect(el.querySelector('textarea#sql')).not.toBeNull();
  });

  it('un error del backend se muestra y libera el botón', async () => {
    // El camino que antes no existía: con el túnel muerto el backend cortaba
    // colgándose, así que la página nunca volvía de `busy`. Ahora el error
    // llega y el botón se rehabilita.
    mockQuery = () => Promise.reject(new Error('El túnel SSM no respondió en localhost:8101'));
    const fixture = TestBed.createComponent(SqlPage);
    const comp = fixture.componentInstance as any;
    comp.envs.set(['dev']);
    comp.sql.set('SELECT * FROM `yappy`.`pedidos`');

    comp.runQuery();
    await settle(fixture);

    expect(comp.busy()).toBe(false);
    expect(comp.result()).toBeNull();
    expect(comp.error()).toContain('El túnel SSM no respondió');
  });
});
