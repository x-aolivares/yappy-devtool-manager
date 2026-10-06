import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { SchemaSyncPage } from './schema-sync';
import { DbService } from '../core/services/db.service';
import { EnvironmentService } from '../core/services/environment.service';
import { ExecuteRequest, SchemaCompileRequest } from '../api-gen/models';

const BASE: Record<string, any> = {
  env_b: 'dev',
  env_a: 'local',
  schema_name: 'yappy',
  status: 'schema_sync',
  create_schema: false,
  tables: ['lines', 'orders'],
  procedures: ['sp_calc'],
  left_alone: [],
  script:
    'DROP TABLE IF EXISTS `yappy`.`orders`;\n' + 'CREATE TABLE `yappy`.`orders` (`id` INT);',
  notes: ['El script no corre en una transacción: corre con autocommit.'],
};

/** Un destino que no tiene el esquema: el caso que rompe con `schema_name` puesto. */
const CREATE_SCRIPT =
  'CREATE DATABASE IF NOT EXISTS `yappy`;\n' +
  'USE `yappy`;\n' +
  'DROP TABLE IF EXISTS `yappy`.`orders`;\n' +
  'CREATE TABLE `yappy`.`orders` (`id` INT);';

// El stub es mutable y module-level a propósito: `TestBed` se configura una vez por
// describe, así que cada test cambia la respuesta desde acá en vez de reconfigurar.
let compileResponse: Record<string, any> = BASE;
let compileRequests: SchemaCompileRequest[] = [];
let executeRequests: ExecuteRequest[] = [];
let objectCalls: Array<{ env: string; schema: string; type: string }> = [];

/** Los objetos que el origen tiene, y los que el destino ya tiene. */
let originTables: string[] = ['lines', 'orders'];
let originProcedures: string[] = ['sp_calc'];
let destinationTables: string[] = ['orders'];

function mockProviders() {
  return [
    {
      provide: EnvironmentService,
      useValue: { list: () => Promise.resolve({ environments: [] }) },
    },
    {
      provide: DbService,
      useValue: {
        listSchemas: () => Promise.resolve({ schemas: ['yappy'] }),
        listObjects: (env: string, schema: string, objectType: string) => {
          objectCalls.push({ env, schema, type: objectType });
          // Un esquema distinto tiene objetos distintos: es lo que hace que la
          // recarga se note en la selección y no sólo en el log de llamadas.
          const esOtro = schema === 'otro';
          const objects =
            objectType === 'table'
              ? env === 'dev'
                ? esOtro
                  ? ['otra']
                  : originTables
                : destinationTables
              : esOtro
                ? []
                : originProcedures;
          return Promise.resolve({
            env,
            schema_name: schema,
            object_type: objectType,
            objects,
          });
        },
        compileSchema: (req: SchemaCompileRequest) => {
          compileRequests.push(req);
          return Promise.resolve(compileResponse);
        },
        executeSql: (req: ExecuteRequest) => {
          executeRequests.push(req);
          return Promise.resolve({
            env: req.env,
            object_type: req.object_type,
            schema_name: req.schema_name,
            results: [
              {
                index: 1,
                sql: 'CREATE TABLE `yappy`.`orders` (`id` INT)',
                ok: true,
                ms: 12,
                error: '',
              },
            ],
            ok_count: 1,
            err_count: 0,
          });
        },
      },
    },
  ];
}

async function setup(): Promise<void> {
  compileResponse = BASE;
  compileRequests = [];
  executeRequests = [];
  objectCalls = [];
  originTables = ['lines', 'orders'];
  originProcedures = ['sp_calc'];
  destinationTables = ['orders'];
  await TestBed.configureTestingModule({
    imports: [SchemaSyncPage],
    providers: [provideRouter([]), ...mockProviders()],
  }).compileComponents();
}

async function settle(fixture: ComponentFixture<SchemaSyncPage>): Promise<void> {
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

function button(el: HTMLElement, label: string): HTMLButtonElement {
  return Array.from(el.querySelectorAll('button')).find((b) =>
    (b.textContent ?? '').includes(label),
  ) as HTMLButtonElement;
}

function textarea(el: HTMLElement): HTMLTextAreaElement {
  return el.querySelector('#schema-sync-script') as HTMLTextAreaElement;
}

/**
 * Arma la página con la cadena completa y genera. Los pasos van con un flush en el
 * medio porque `app-schema-select` se vacía solo cuando cambia el ambiente.
 */
async function generate(response: Record<string, any> = {}): Promise<{
  fixture: ComponentFixture<SchemaSyncPage>;
  comp: any;
  el: HTMLElement;
}> {
  compileResponse = { ...BASE, ...response };
  const { fixture, comp } = await ready();
  comp.generate();
  await settle(fixture);
  return { fixture, comp, el: fixture.nativeElement as HTMLElement };
}

/**
 * La página con origen, destino y esquema cargados, y la lista de objetos ya leída.
 *
 * Es el estado en el que el usuario realmente elige: sin esto no hay tabla y no
 * hay nada que marcar.
 */
async function ready(): Promise<{
  fixture: ComponentFixture<SchemaSyncPage>;
  comp: any;
  el: HTMLElement;
}> {
  const fixture = TestBed.createComponent(SchemaSyncPage);
  const comp = fixture.componentInstance as any;
  comp.envB.set('dev');
  await settle(fixture);
  comp.envA.set('local');
  await settle(fixture);
  comp.schema.set('yappy');
  await settle(fixture);
  return { fixture, comp, el: fixture.nativeElement as HTMLElement };
}

describe('SchemaSyncPage generación', () => {
  beforeEach(setup);

  it('deja el script generado en el textarea', async () => {
    const { comp, el } = await generate();

    expect(comp.script()).toContain('DROP TABLE IF EXISTS `yappy`.`orders`');
    expect(textarea(el).value).toContain('DROP TABLE IF EXISTS `yappy`.`orders`');
  });

  it('muestra el estado, los conteos y todas las notas', async () => {
    const { el } = await generate({ notes: ['Primera nota.', 'Segunda nota.'] });

    expect(el.textContent).toContain('Se sincroniza el schema en la región Destino');
    expect(el.textContent).toContain('dev → local');
    expect(el.textContent).toContain('2 tabla(s) y 1 procedimiento(s)');
    expect(el.textContent).toContain('Primera nota.');
    expect(el.textContent).toContain('Segunda nota.');
  });

  it('reporta que el destino no tiene el esquema', async () => {
    const { el } = await generate({ create_schema: true });

    expect(el.textContent).toContain('El destino no tiene el esquema');
    expect(el.textContent).toContain('el script lo crea');
  });

  it('lista las tablas del destino que no se tocan', async () => {
    const { el } = await generate({ left_alone: ['legacy', 'audit'] });

    expect(el.textContent).toContain('quedan sin tocar 2 tabla(s) que el origen no tiene');
    expect(el.textContent).toContain('legacy');
    expect(el.textContent).toContain('audit');
    expect(el.textContent).toContain('No aparecen en el script');
  });

  it('no inventa tablas sin tocar cuando el destino no tiene de sobra', async () => {
    const { el } = await generate({ left_alone: [] });

    expect(el.textContent).not.toContain('quedan sin tocar');
  });

  it('manda la lista exacta de objetos marcados', async () => {
    const { fixture, comp } = await ready();
    // Todo arranca marcado: sincronizar el esquema significa el esquema.
    comp.generate();
    await settle(fixture);

    expect(compileRequests.length).toBe(1);
    // La lista viaja explícita, sin los flags decidiendo por su cuenta. Es lo que
    // garantiza que el backend no compile lo que el usuario desmarcó.
    expect(compileRequests[0].tables).toEqual(['lines', 'orders']);
    expect(compileRequests[0].procedures).toEqual(['sp_calc']);
    expect(compileRequests[0]).toMatchObject({
      env_b: 'dev',
      env_a: 'local',
      schema_name: 'yappy',
      include_tables: true,
      include_procedures: true,
    });
  });

  it('desmarcar un objeto lo saca de la request', async () => {
    const { fixture, comp } = await ready();

    comp.toggle('orders', false);
    await settle(fixture);
    comp.generate();
    await settle(fixture);

    expect(compileRequests[0].tables).toEqual(['lines']);
    expect(compileRequests[0].procedures).toEqual(['sp_calc']);
  });

  it('deshabilita Generar cuando no queda nada marcado', async () => {
    const { fixture, comp, el } = await ready();
    comp.toggleAll(false);
    await settle(fixture);

    expect(comp.scopeSelected()).toBe(false);
    expect(comp.canGenerate()).toBe(false);
    expect(button(el, 'Generar').disabled).toBe(true);
    expect(el.textContent).toContain('Marcá al menos una tabla o un stored procedure');
  });

  it('vuelve a habilitar Generar si queda un solo objeto marcado', async () => {
    const { fixture, comp, el } = await ready();
    comp.toggleAll(false);
    await settle(fixture);
    expect(comp.canGenerate()).toBe(false);

    comp.toggle('sp_calc', true);
    await settle(fixture);

    expect(comp.scopeSelected()).toBe(true);
    expect(comp.canGenerate()).toBe(true);
    expect(button(el, 'Generar').disabled).toBe(false);
  });

  it('"Todos" a medias se muestra indeterminado, no marcado', async () => {
    const { fixture, comp } = await ready();

    // Con 3 objetos y 2 marcados, la casilla global no puede decir "todos": saying
    // it would make one more click silently drop the third.
    comp.toggle('sp_calc', false);
    await settle(fixture);

    expect(comp.allSelected()).toBe(false);
    expect(comp.someSelected()).toBe(true);
    expect(comp.selectedCount()).toBe(2);
  });

  it('un origen sin objetos no habilita nada y lo dice', async () => {
    originTables = [];
    originProcedures = [];
    const { fixture, comp, el } = await ready();

    expect(comp.objectRows()).toEqual([]);
    expect(comp.canGenerate()).toBe(false);
    expect(el.textContent).toContain('no tiene tablas ni procedures');
  });

  it('no genera con origen y destino iguales', async () => {
    const fixture = TestBed.createComponent(SchemaSyncPage);
    const comp = fixture.componentInstance as any;
    comp.envB.set('dev');
    comp.envA.set('dev');
    comp.schema.set('yappy');
    await settle(fixture);

    expect(comp.sameEnv()).toBe(true);
    expect(comp.canGenerate()).toBe(false);
    expect(comp.generateHint()).toBe('El origen y el destino tienen que ser distintos.');
    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'no pueden ser el mismo ambiente',
    );
  });

  it('descarta el script generado cuando cambia el esquema', async () => {
    const { fixture, comp } = await generate();
    expect(comp.script()).not.toBe('');

    comp.schema.set('otro');
    await settle(fixture);

    expect(comp.script()).toBe('');
    expect(comp.result()).toBeNull();
    expect(comp.canSync()).toBe(false);
  });

  it('descarta el script generado cuando cambia la selección', async () => {
    const { fixture, comp } = await generate();
    expect(comp.script()).not.toBe('');

    comp.toggle('orders', false);
    await settle(fixture);

    // El script en el editor ya no describe lo que el usuario pidió: correrlo
    // sincronizaría una tabla que acaba de sacar del alcance.
    expect(comp.script()).toBe('');
    expect(comp.result()).toBeNull();
  });

  it('deja el editor vacío cuando el backend no manda script', async () => {
    const { comp } = await generate({ script: '' });

    expect(comp.script()).toBe('');
    expect(comp.canSync()).toBe(false);
  });
});

describe('SchemaSyncPage la tabla de objetos', () => {
  beforeEach(setup);

  it('lista cada objeto del origen con su tipo y su casilla', async () => {
    const { el } = await ready();

    // Se afirma sobre las filas y los ids, no sobre el título del panel: el
    // título es copy, las filas son la estructura que hace falta.
    const filas = [...el.querySelectorAll('table.filter-table tbody tr')];
    expect(filas.length).toBe(3);
    expect(el.querySelector('#sync-obj-orders')).not.toBeNull();
    expect(el.querySelector('#sync-obj-lines')).not.toBeNull();
    expect(el.querySelector('#sync-obj-sp_calc')).not.toBeNull();
    // Tres columnas y ninguna más: `Objeto`, `Tipo` y `Existe en <destino>`. La
    // primera columna sin nombre que había antes duplicaba el nombre del objeto
    // en dos celdas.
    const headers = [...el.querySelectorAll('table.filter-table thead th')].map((th) =>
      th.textContent?.trim(),
    );
    expect(headers).toEqual(['Objeto', 'Tipo', 'Existe en local']);
    expect(el.textContent).toContain('tabla');
    expect(el.textContent).toContain('procedure');
  });

  it('el nombre del objeto aparece una sola vez por fila', async () => {
    const { el } = await ready();

    // Una fila por objeto, con el nombre en la celda de la casilla. Repetirlo en
    // dos columnas hacía que un nombre largo se leyera dos veces y que el ancho
    // de la tabla fuera el doble de lo necesario.
    for (const nombre of ['orders', 'lines', 'sp_calc']) {
      const fila = [...el.querySelectorAll('table.filter-table tbody tr')].find(
        (f) => f.querySelector('#sync-obj-' + nombre) !== null,
      );
      const celdas = fila ? [...fila.querySelectorAll('td')] : [];
      expect(celdas.length).toBe(3);
      const apariciones = (celdas[0].textContent ?? '').split(nombre).length - 1;
      expect(apariciones).toBe(1);
    }
  });

  it('todo arranca marcado y el conteo lo dice', async () => {
    const { fixture, comp, el } = await ready();

    expect(comp.selected()).toEqual({ tables: ['lines', 'orders'], procedures: ['sp_calc'] });
    expect(comp.allSelected()).toBe(true);
    expect(comp.objectsLoading()).toBe(false);
    expect(comp.objectRows().length).toBe(3);
    expect(el.textContent).toContain('3 de 3 marcados');
  });

  it('dice si el destino ya tiene el objeto', async () => {
    const { el } = await ready();

    // `orders` está en el destino y `lines` no: esa diferencia es la que dice
    // qué filas se van a perder, y por eso la tabla la muestra.
    const fila = [...el.querySelectorAll('table.filter-table tbody tr')].find(
      (f) => f.querySelector('#sync-obj-orders') !== null,
    );
    expect(fila?.textContent).toContain('Sí');

    const nueva = [...el.querySelectorAll('table.filter-table tbody tr')].find(
      (f) => f.querySelector('#sync-obj-lines') !== null,
    );
    expect(nueva?.textContent).toContain('No, se crea');
  });

  it('desmarcar en la tabla cambia la selección y el botón de todos', async () => {
    const { fixture, comp, el } = await ready();

    (el.querySelector('#sync-obj-orders') as HTMLInputElement).click();
    await settle(fixture);

    expect(comp.selected().tables).toEqual(['lines']);
    expect(comp.selected().procedures).toEqual(['sp_calc']);
    expect(el.textContent).toContain('2 de 3 marcados');
  });

  it('"Todos" marca y desmarca la lista entera', async () => {
    const { fixture, comp, el } = await ready();

    const todos = el.querySelector('#schema-sync-all') as HTMLInputElement;
    todos.click();
    await settle(fixture);
    expect(comp.selected()).toEqual({ tables: [], procedures: [] });
    expect(el.textContent).toContain('0 de 3 marcados');

    todos.click();
    await settle(fixture);
    expect(comp.selected()).toEqual({ tables: ['lines', 'orders'], procedures: ['sp_calc'] });
  });

  it('pregunta tablas y procedures del origen, y las tablas del destino', async () => {
    await ready();

    const delOrigen = objectCalls.filter((c) => c.env === 'dev');
    expect(delOrigen.map((c) => c.type).sort()).toEqual(['procedure', 'table']);
    // La tercera es la del destino, y sólo de tablas: la que responde "ya está".
    expect(objectCalls.some((c) => c.env === 'local' && c.type === 'table')).toBe(true);
  });

  it('cambiar de esquema recarga la lista y limpia la selección', async () => {
    const { fixture, comp } = await ready();
    expect(comp.selected().tables).toEqual(['lines', 'orders']);

    comp.schema.set('otro');
    await settle(fixture);

    // `otro` tiene una sola tabla y ningún procedure. Los nombres marcados
    // anterior eran de `yappy` y probablemente ni existen acá: dejarlos sería
    // mandar una lista que el backend va a rechazar.
    expect(objectCalls.some((c) => c.schema === 'otro' && c.env === 'dev')).toBe(true);
    expect(comp.selected()).toEqual({ tables: ['otra'], procedures: [] });
    expect(comp.selected().tables).not.toContain('orders');
  });

  it('el aviso de filas perdidas cuenta tablas, no objetos', async () => {
    const { fixture, comp, el } = await ready();
    // Sólo un procedure marcado: no hay ninguna tabla que recrear, y decirlo evita
    // un aviso de "se pierden filas" sobre algo que no toca tablas.
    comp.toggleAll(false);
    comp.toggle('sp_calc', true);
    await settle(fixture);

    expect(el.textContent).toContain('no se toca ninguna tabla');
  });

  it('con tablas marcadas el aviso dice cuántas filas se pierden', async () => {
    const { fixture, comp, el } = await ready();
    comp.toggleAll(false);
    comp.toggle('orders', true);
    await settle(fixture);

    expect(el.textContent).toContain('1 tabla');
    expect(el.textContent).toContain('se pierden');
  });
});

describe('SchemaSyncPage sincronizar', () => {
  beforeEach(async () => {
    await setup();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  afterEach(() => vi.restoreAllMocks());

  it('ejecuta con el esquema vacío y como script, aunque el destino no lo tenga', async () => {
    const { fixture, comp } = await generate({ create_schema: true, script: CREATE_SCRIPT });
    expect(comp.result()!.create_schema).toBe(true);

    comp.run();
    await settle(fixture);

    expect(executeRequests.length).toBe(1);
    expect(executeRequests[0].env).toBe('local');
    // Vacío a propósito: el `USE` y el `CREATE DATABASE` del propio script hacen el
    // trabajo. Mandar `yappy` haría fallar el primer statement con "Unknown
    // database" contra un destino que todavía no tiene el esquema.
    expect(executeRequests[0].schema_name).toBe('');
    expect(executeRequests[0].object_type).toBe('script');
    expect(executeRequests[0].code).toBe(CREATE_SCRIPT);
  });

  it('un script con DROP TABLE pide confirmación y lo dice claro', async () => {
    const { fixture, comp } = await generate();

    comp.run();
    await settle(fixture);

    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringContaining('¿Sincronizar yappy de dev a local?'),
    );
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('borra y vuelve a crear'));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('se pierden'));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('No es una fusión'));
  });

  it('declinar la confirmación no llama a la API', async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    const { fixture, comp } = await generate();

    comp.run();
    await settle(fixture);

    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(executeRequests).toHaveLength(0);
    expect(comp.executed()).toBeNull();
    expect(comp.busy()).toBe(false);
  });

  it('omite el aviso de DROP para un script que no borra nada, pero igual pregunta', async () => {
    const fixture = TestBed.createComponent(SchemaSyncPage);
    const comp = fixture.componentInstance as any;
    comp.envA.set('local');
    comp.script.set('CREATE TABLE `yappy`.`orders` (`id` INT);');
    await settle(fixture);

    comp.run();
    await settle(fixture);

    const mensaje = vi.mocked(window.confirm).mock.calls[0][0];
    expect(mensaje).not.toContain('No es una fusión');
    // La confirmación va siempre: sincronizar un esquema nunca es una operación de
    // lectura, aunque el texto que se pegó no borre nada.
    expect(mensaje).toContain('¿Sincronizar');
    expect(executeRequests.length).toBe(1);
  });

  it('renderiza el resultado sentencia por sentencia', async () => {
    const { fixture, comp, el } = await generate();

    comp.run();
    await settle(fixture);

    expect(comp.executed()!.err_count).toBe(0);
    expect(el.textContent).toContain('Sentencias ejecutadas');
    expect(el.textContent).toContain('1 sentencia(s) OK');
    expect(el.textContent).toContain('#1');
    expect(el.textContent).toContain('CREATE TABLE `yappy`.`orders` (`id` INT)');
    expect(el.textContent).toContain('12 ms');
    expect(el.querySelector('pre.stmt-preview')?.textContent).toContain('CREATE TABLE');
  });

  it('no sincroniza con el editor vacío', async () => {
    const fixture = TestBed.createComponent(SchemaSyncPage);
    const comp = fixture.componentInstance as any;
    comp.envA.set('local');
    await settle(fixture);

    expect(comp.canSync()).toBe(false);
    expect(button(fixture.nativeElement as HTMLElement, 'Sincronizar en').disabled).toBe(true);

    comp.run();
    await settle(fixture);

    expect(window.confirm).not.toHaveBeenCalled();
    expect(executeRequests).toHaveLength(0);
  });
});
