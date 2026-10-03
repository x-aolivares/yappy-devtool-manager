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
  const fixture = TestBed.createComponent(SchemaSyncPage);
  const comp = fixture.componentInstance as any;
  comp.envB.set('dev');
  await settle(fixture);
  comp.envA.set('local');
  await settle(fixture);
  comp.schema.set('yappy');
  await settle(fixture);
  comp.generate();
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

  it('manda el alcance elegido en la request', async () => {
    const fixture = TestBed.createComponent(SchemaSyncPage);
    const comp = fixture.componentInstance as any;
    comp.envB.set('dev');
    await settle(fixture);
    comp.envA.set('local');
    await settle(fixture);
    comp.schema.set('yappy');
    comp.includeProcedures.set(false);
    await settle(fixture);
    comp.generate();
    await settle(fixture);

    expect(compileRequests.length).toBe(1);
    expect(compileRequests[0]).toEqual({
      env_b: 'dev',
      env_a: 'local',
      schema_name: 'yappy',
      include_tables: true,
      include_procedures: false,
    });
  });

  it('deshabilita Generar cuando no queda nada en el alcance', async () => {
    const fixture = TestBed.createComponent(SchemaSyncPage);
    const comp = fixture.componentInstance as any;
    comp.envB.set('dev');
    await settle(fixture);
    comp.envA.set('local');
    await settle(fixture);
    comp.schema.set('yappy');
    comp.includeTables.set(false);
    comp.includeProcedures.set(false);
    await settle(fixture);

    expect(comp.scopeSelected()).toBe(false);
    expect(comp.canGenerate()).toBe(false);
    expect(button(fixture.nativeElement as HTMLElement, 'Generar').disabled).toBe(true);
    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'Marcá al menos tablas o stored procedures',
    );
  });

  it('vuelve a habilitar Generar si queda un tipo de objeto marcado', async () => {
    const fixture = TestBed.createComponent(SchemaSyncPage);
    const comp = fixture.componentInstance as any;
    comp.envB.set('dev');
    await settle(fixture);
    comp.envA.set('local');
    await settle(fixture);
    comp.schema.set('yappy');
    comp.includeTables.set(false);
    comp.includeProcedures.set(false);
    await settle(fixture);

    comp.includeProcedures.set(true);
    await settle(fixture);

    expect(comp.canGenerate()).toBe(true);
    expect(button(fixture.nativeElement as HTMLElement, 'Generar').disabled).toBe(false);
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

  it('descarta el script generado cuando cambia el alcance', async () => {
    const { fixture, comp } = await generate();
    expect(comp.script()).not.toBe('');

    comp.includeTables.set(false);
    await settle(fixture);

    expect(comp.script()).toBe('');
    expect(comp.result()).toBeNull();
  });

  it('deja el editor vacío cuando el backend no manda script', async () => {
    const { comp } = await generate({ script: '' });

    expect(comp.script()).toBe('');
    expect(comp.canSync()).toBe(false);
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
