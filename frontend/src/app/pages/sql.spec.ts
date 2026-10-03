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

  it('el editor de la consulta sigue siendo un textarea#sql', () => {
    const fixture = TestBed.createComponent(SqlPage);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    // El id lo consulta el resto del flujo; el refactor visual no lo renombra.
    expect(el.querySelector('textarea#sql')).not.toBeNull();
  });
});
