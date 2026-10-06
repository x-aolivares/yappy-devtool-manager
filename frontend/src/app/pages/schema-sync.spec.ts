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
let destinationProcedures: string[] = ['sp_sync'];
/** Falla sólo la lectura del destino, para probar que la lista igual sirve. */
let destinationFails = false;

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
          const esDestino = env !== 'dev';
          // Un esquema distinto tiene objetos distintos: es lo que hace que la
          // recarga se note en la selección y no sólo en el log de llamadas.
          const esOtro = schema === 'otro';
          if (esDestino && destinationFails) {
            return Promise.reject(new Error('El destino no responde.'));
          }
          const objects =
            objectType === 'table'
              ? esDestino
                ? destinationTables
                : esOtro
                  ? ['otra']
                  : originTables
              : esDestino
                ? destinationProcedures
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
  destinationProcedures = ['sp_sync'];
  destinationFails = false;
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

  it('pregunta tablas y procedures del origen, y las dos clases del destino', async () => {
    await ready();

    const delOrigen = objectCalls.filter((c) => c.env === 'dev');
    expect(delOrigen.map((c) => c.type).sort()).toEqual(['procedure', 'table']);

    // **Las dos del destino.** Preguntar sólo por tablas dejaba a todo procedure
    // marcado "No, se crea" para siempre, aunque ya estuviera compilado: el bug
    // que hacía que un procedure bien compilado pareciera recién a crearse.
    const delDestino = objectCalls.filter((c) => c.env === 'local');
    expect(delDestino.map((c) => c.type).sort()).toEqual(['procedure', 'table']);
  });

  it('un procedure que ya está en el destino no dice que se crea', async () => {
    originProcedures = ['sp_calc', 'sp_sync'];
    const { fixture, comp, el } = await ready();

    const fila = [...el.querySelectorAll('table.filter-table tbody tr')].find(
      (f) => f.querySelector('#sync-obj-sp_sync') !== null,
    );
    expect(fila?.textContent).toContain('Sí');

    // Y el que sí falta, sigue faltando. La columna distingue las dos cosas.
    const otro = [...el.querySelectorAll('table.filter-table tbody tr')].find(
      (f) => f.querySelector('#sync-obj-sp_calc') !== null,
    );
    expect(otro?.textContent).toContain('No, se crea');
  });

  it('una tabla y un procedure del mismo nombre no se confunden', async () => {
    // El destino indexa por clase, no en un set plano: sin eso, un procedure que
    // existe se reportaría como tabla porque el nombre coincidiera.
    originTables = ['sp_sync'];
    originProcedures = ['sp_sync'];
    destinationTables = [];
    destinationProcedures = ['sp_sync'];

    const { fixture, comp } = await ready();

    // Dos filas, no una: la tabla `sp_sync` no está en el destino y el procedure
    // del mismo nombre sí. Un set plano de nombres habría reportado "Sí" para las
    // dos, o "No" para las dos.
    const filas = comp.objectRows().filter((r: { name: string }) => r.name === 'sp_sync');
    expect(filas.length).toBe(2);

    const tabla = filas.find((r: { kind: string }) => r.kind === 'table');
    const procedure = filas.find((r: { kind: string }) => r.kind === 'procedure');
    expect(tabla.inDestination).toBe(false);
    expect(procedure.inDestination).toBe(true);
  });

  it('con el mismo nombre, desmarcar una fila no toca la otra', async () => {
    originTables = ['sp_sync'];
    originProcedures = ['sp_sync'];
    destinationTables = [];
    destinationProcedures = ['sp_sync'];

    const { fixture, comp } = await ready();
    comp.toggle('sp_sync', false, 'procedure');
    await settle(fixture);

    // Buscar por nombre solamente habría marcado la tabla —la primera fila— y
    // dejado el procedure sin poder sacarse del alcance.
    expect(comp.objectRows().find((r: { kind: string }) => r.kind === 'procedure').selected).toBe(
      false,
    );
    expect(comp.objectRows().find((r: { kind: string }) => r.kind === 'table').selected).toBe(true);
    expect(comp.selected()).toEqual({ tables: ['sp_sync'], procedures: [] });
  });

  it('si el destino no responde, la lista igual sirve y lo avisa', async () => {
    destinationFails = true;
    const { fixture, comp, el } = await ready();

    // Perder la columna es un problema; perder la lista sería overkill.
    expect(comp.objectRows().length).toBe(3);
    expect(comp.destinationError()).toContain('El destino no responde');
    expect(el.textContent).toContain('La columna "Existe en" no se sabe');
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

  // --- la tabla de objetos se pagina --------------------------------------
  //
  // Un esquema real tiene cientos de objetos. Paginar una tabla de casillas tiene
  // un riesgo propio que una grilla de datos no tiene: **una fila desmarcada en
  // otra página no se ve**, y sin contador el usuario desmarca cuatro y no tiene
  // forma de saber que le faltan cuatro.

  it('con muchos objetos muestra una sola página', async () => {
    originTables = Array.from({ length: 60 }, (_, i) => `tabla_${i}`);
    originProcedures = [];
    const { fixture, comp, el } = await ready();

    expect(el.querySelectorAll('table.filter-table tbody tr').length).toBe(25);
    expect(el.querySelector('.table-pagination')?.textContent).toContain('1–25 de 60');
  });

  it('el contador de marcados habla del total, no de la página', async () => {
    originTables = Array.from({ length: 60 }, (_, i) => `tabla_${i}`);
    originProcedures = [];
    const { fixture, comp, el } = await ready();

    comp.page.next();
    await settle(fixture);

    // La fila desmarcada está en la página 2 y el contador tiene que contarla:
    // si dijera "25 de 25" el usuario no sabría que hay cuatro fuera de pantalla.
    comp.toggle('tabla_30', false);
    await settle(fixture);

    expect(comp.page.current()).toBe(2);
    expect(comp.selected().tables).toHaveLength(59);
    expect(el.textContent).toContain('59 de 60 marcados');
  });

  it('"Todos" marca la lista entera, no sólo la página visible', async () => {
    originTables = Array.from({ length: 60 }, (_, i) => `tabla_${i}`);
    originProcedures = [];
    const { fixture, comp } = await ready();

    // Marcar la página 1 y volver a la 2: si "Todos" fuera sobre lo visible, la
    // 2 quedaría con todas sus casillas apagadas y el botón en un estado que no
    // corresponde a nada.
    comp.page.next();
    await settle(fixture);
    comp.toggleAll(false);
    await settle(fixture);
    expect(comp.selected().tables).toEqual([]);

    comp.toggleAll(true);
    await settle(fixture);

    expect(comp.selected().tables).toHaveLength(60);
    expect(comp.allSelected()).toBe(true);
    expect(comp.page.total()).toBe(60);
  });

  it('"Todos" a medias se mide sobre el total, no sobre la página', async () => {
    originTables = Array.from({ length: 60 }, (_, i) => `tabla_${i}`);
    originProcedures = [];
    const { fixture, comp } = await ready();

    comp.page.next();
    await settle(fixture);
    comp.toggle('tabla_30', false);
    await settle(fixture);

    // 59 de 60: sigue siendo "todos" a medias, no "todos en esta página".
    expect(comp.allSelected()).toBe(false);
    expect(comp.someSelected()).toBe(true);
    expect(comp.selectedCount()).toBe(59);
  });

  it('el script se genera con todos los marcados, no con los de la página', async () => {
    // El motivo de que esto no se rompa nunca: el request manda `selected()`,
    // que no está paginado. Si se paginara, sincronizarías 25 objetos de los 60
    // que marcaste — y como el script reemplaza, los otros 35 se quedarían como
    // están sin que nada lo diga.
    originTables = Array.from({ length: 60 }, (_, i) => `tabla_${i}`);
    originProcedures = [];
    const { fixture, comp } = await ready();

    comp.page.next();
    await settle(fixture);
    comp.toggle('tabla_30', false);
    await settle(fixture);

    expect(comp.selected().tables).toHaveLength(59);

    comp.generate();
    await settle(fixture);

    expect(compileRequests[0].tables).toHaveLength(59);
    expect(compileRequests[0].tables).not.toContain('tabla_30');
  });

  // --- filtrar la tabla de objetos ---------------------------------------

  it('buscar encuentra por nombre, sin importar el tipo', async () => {
    originTables = ['yappy_payment'];
    originProcedures = ['sp_sync_payment'];
    const { fixture, comp, el } = await ready();

    // "payment" está en dos objetos de clases distintas: un solo control tiene que
    // encontrar los dos, que es lo que lo hace útil en un esquema real.
    comp.search.query.set('payment');
    await settle(fixture);

    // Tablas primero, procedures después: es el orden de `objectRows`, que no es
    // alfabético sino por clase.
    expect(comp.page.visible().map((r: { name: string }) => r.name)).toEqual([
      'yappy_payment',
      'sp_sync_payment',
    ]);
  });

  it('el filtro busca también por tipo', async () => {
    originTables = ['yappy_payment'];
    originProcedures = ['sp_sync_payment'];
    const { fixture, comp, el } = await ready();

    comp.search.query.set('procedure');
    await settle(fixture);
    expect(comp.page.visible().map((r: { name: string }) => r.name)).toEqual(['sp_sync_payment']);
  });

  it('el contador de marcados sigue hablando del total, con filtro puesto', async () => {
    originTables = ['yappy_payment', 'audit_log', 'orders'];
    originProcedures = ['sp_sync_payment'];
    const { fixture, comp, el } = await ready();

    // Es la garantía que hace que el filtro no pueda mentir: si el contador
    // hablara de lo filtrado, filtrar a 1 objeto haría creer que sólo hay una
    // tabla marcada de cuatro.
    comp.search.query.set('yappy');
    await settle(fixture);

    expect(comp.page.visible()).toHaveLength(1);
    expect(comp.selectedCount()).toBe(4);
    expect(el.textContent).toContain('4 de 4 marcados');
  });

  it('"Todos" con filtro puesto sigue marcando la lista entera', async () => {
    originTables = ['yappy_payment', 'audit_log', 'orders'];
    originProcedures = ['sp_sync_payment'];
    const { fixture, comp } = await ready();

    // Filtrar es mirar, no elegir. Si "Todos" marcara sólo lo visible, filtrar por
    // `yappy` y tocar "Todos" desmarcaría tres objetos que el usuario nunca vio
    // desaparecer.
    comp.search.query.set('yappy');
    await settle(fixture);
    comp.toggleAll(false);
    await settle(fixture);
    expect(comp.selected()).toEqual({ tables: [], procedures: [] });

    comp.toggleAll(true);
    await settle(fixture);

    expect(comp.selected().tables).toHaveLength(3);
    expect(comp.selected().procedures).toHaveLength(1);
  });

  it('el script lleva todos los marcados aunque el filtro muestre uno', async () => {
    originTables = ['yappy_payment', 'audit_log', 'orders'];
    originProcedures = ['sp_sync_payment'];
    const { fixture, comp } = await ready();

    comp.search.query.set('yappy');
    await settle(fixture);
    comp.generate();
    await settle(fixture);

    expect(compileRequests[0].tables).toHaveLength(3);
    expect(compileRequests[0].procedures).toHaveLength(1);
  });

  it('filtrar devuelve a la primera página', async () => {
    originTables = Array.from({ length: 60 }, (_, i) => `tabla_${i}`);
    originProcedures = [];
    const { fixture, comp } = await ready();

    comp.page.goTo(3);
    await settle(fixture);
    comp.search.query.set('tabla_1');
    await settle(fixture);

    // Sin el reset, la barra quedaría diciendo "Página 3 de 1" con la tabla vacía.
    expect(comp.page.current()).toBe(1);
    expect(comp.page.total()).toBeGreaterThan(0);
  });

  it('cambiar de esquema limpia el filtro', async () => {
    originTables = ['yappy_payment', 'audit_log', 'orders'];
    originProcedures = [];
    const { fixture, comp } = await ready();

    comp.search.query.set('yappy');
    await settle(fixture);
    expect(comp.page.total()).toBe(1);

    // El texto de búsqueda era de otro esquema: dejarlo puesto deja la tabla
    // vacía sin explicación de por qué.
    comp.schema.set('otro');
    await settle(fixture);

    expect(comp.search.query()).toBe('');
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

  it('vuelve a preguntar al destino si ya tiene lo que se acaba de correr', async () => {
    const { fixture, comp } = await generate();
    // Nada del script está en el destino todavía: `sp_calc` no está en la lista
    // que el mock usa para el destino.
    expect(comp.objectRows().find((r: { name: string }) => r.name === 'sp_calc').inDestination).toBe(
      false,
    );
    const antes = objectCalls.filter((c) => c.env === 'local').length;

    comp.run();
    await settle(fixture);

    // El script corrió, así que se vuelve a preguntar. La columna era un snapshot
    // de cuando cargó la página y seguía diciendo "No, se crea" para lo que el
    // script acababa de crear.
    expect(objectCalls.filter((c) => c.env === 'local').length).toBeGreaterThan(antes);
  });

  it('el refresh posterior no toca la selección', async () => {
    const { fixture, comp } = await generate();
    comp.toggle('orders', false);
    await settle(fixture);

    comp.run();
    await settle(fixture);

    // Correr no puede volver a marcar lo que el usuario sacó: la selección es
    // suya, y pisarla haría que el próximo script incluyera una tabla que se
    // había sacado a propósito.
    expect(comp.selected().tables).toEqual(['lines']);
  });

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
