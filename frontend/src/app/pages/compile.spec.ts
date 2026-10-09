import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { CompilePage } from './compile';
import { DbService } from '../core/services/db.service';
import { EnvironmentService } from '../core/services/environment.service';
import { ExecuteRequest } from '../api-gen/models';

/**
 * Click en el botón de confirmar del `NoticeModal`.
 *
 * `run()` ya no escribe nada: deja la ejecución pendiente y abre el modal, así
 * que un test que quiera ver el `executeSql` tiene que confirmar primero. Es el
 * mismo camino que el del usuario —el botón emite `confirmed`— y por eso el test
 * no depende de que `confirmar()` sea público ni de su firma.
 *
 * No hay `sleep` ni espera arbitraria: el modal es un signal y el click dispara
 * un `output`, así que con un `detectChanges()` alcanza.
 */
function confirmarEnModal(el: HTMLElement): void {
  const boton = el.querySelector<HTMLButtonElement>('.notice-modal__confirm');
  if (!boton) throw new Error('No hay modal de confirmación abierto');
  boton.click();
}

/** Click en "Cancelar" del modal: la vía segura, la que no ejecuta nada. */
function cancelarEnModal(el: HTMLElement): void {
  const boton = el.querySelector<HTMLButtonElement>('.notice-modal__cancel');
  if (!boton) throw new Error('No hay modal de confirmación abierto');
  boton.click();
}

const BASE: Record<string, any> = {
  env_a: 'local',
  env_b: 'dev',
  object_type: 'table',
  schema_name: 'yappy',
  object_name: 'orders',
  status: 'replace_in_a',
  code_a: 'CREATE TABLE `orders` (`id` INT)',
  code_b: 'CREATE TABLE `orders` (`id` INT, `newcol` INT)',
  script: 'DROP TABLE IF EXISTS `yappy`.`orders`;\nCREATE TABLE `orders` (`id` INT, `newcol` INT)',
  notes: [],
};

describe('CompilePage script generado', () => {
  let mockCompile: () => Promise<Record<string, any>>;

  beforeEach(async () => {
    mockCompile = () => Promise.resolve(BASE);
    await TestBed.configureTestingModule({
      imports: [CompilePage],
      providers: [
        provideRouter([]),
        {
          provide: EnvironmentService,
          useValue: { list: () => Promise.resolve({ environments: [] }) },
        },
        {
          provide: DbService,
          useValue: {
            listSchemas: () => Promise.resolve({ schemas: ['yappy'] }),
            listObjects: () => Promise.resolve({ objects: ['orders'] }),
            compile: () => mockCompile(),
          },
        },
      ],
    }).compileComponents();
  });

  async function generate(response: Record<string, any>) {
    mockCompile = () => Promise.resolve({ ...BASE, ...response });
    const fixture = TestBed.createComponent(CompilePage);
    const comp = fixture.componentInstance as any;
    comp.envB.set('dev');
    comp.envA.set('local');
    comp.schema.set('yappy');
    comp.objectName.set('orders');
    comp.generate();
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return { fixture, comp, el: fixture.nativeElement as HTMLElement };
  }

  it('usa el script cuando viene informado', async () => {
    const { comp } = await generate({
      status: 'replace_in_a',
      script: 'DROP TABLE IF EXISTS `yappy`.`orders`;\nCREATE TABLE `orders` (`id` INT);',
    });
    expect(comp.script()).toBe(
      'DROP TABLE IF EXISTS `yappy`.`orders`;\nCREATE TABLE `orders` (`id` INT);',
    );
  });

  it('cae a code_b cuando script es null (objeto que no está en el origen)', async () => {
    const { comp } = await generate({
      status: 'none',
      script: null,
      code_b: 'CREATE TABLE `x` (`id` INT)',
    });
    expect(comp.script()).toBe('CREATE TABLE `x` (`id` INT)');
  });

  it('deja el editor vacío cuando no hay script ni code_b', async () => {
    const { comp } = await generate({ status: 'none', script: null, code_b: null, code_a: null });
    expect(comp.script()).toBe('');
  });

  it('deja el botón Compilar habilitado con el contenido sembrado', async () => {
    const { comp, el } = await generate({ status: 'replace_in_a' });
    expect(comp.canExecute()).toBe(true);
    const run = Array.from(el.querySelectorAll('button')).find((b) =>
      (b.textContent ?? '').includes('Compilar en'),
    ) as HTMLButtonElement;
    expect(run.disabled).toBe(false);
  });

  it('avisa que compilar una tabla borra las filas del destino', async () => {
    const { comp, el } = await generate({ status: 'replace_in_a' });
    expect(comp.replaceNotice()).toContain('borra la tabla y todas sus filas en local');
    expect(comp.replaceNotice()).toContain('editá el script y dejá solo');
    expect(el.textContent).toContain('borra la tabla y todas sus filas en local');
  });

  it('no avisa de borrado cuando la tabla no está en el destino', async () => {
    const { comp, el } = await generate({ status: 'missing_in_a' });
    expect(comp.replaceNotice()).toBeNull();
    expect(el.textContent).not.toContain('borra la tabla y todas sus filas');
  });

  it('no avisa de borrado para un stored procedure', async () => {
    const { comp } = await generate({
      status: 'different',
      object_type: 'procedure',
      script: 'DROP PROCEDURE IF EXISTS `yappy`.`p`;\nCREATE PROCEDURE `p`() BEGIN SELECT 1; END',
    });
    expect(comp.replaceNotice()).toBeNull();
  });

  it('descarta el contenido sembrado cuando cambia un selector', async () => {
    const { fixture, comp } = await generate({ status: 'replace_in_a' });
    expect(comp.script()).not.toBe('');

    comp.objectName.set('other');
    fixture.detectChanges();

    expect(comp.script()).toBe('');
    expect(comp.canExecute()).toBe(false);
  });
});

describe('CompilePage origen = script', () => {
  let sent: ExecuteRequest[];

  beforeEach(async () => {
    sent = [];
    await TestBed.configureTestingModule({
      imports: [CompilePage],
      providers: [
        provideRouter([]),
        {
          provide: EnvironmentService,
          useValue: { list: () => Promise.resolve({ environments: [] }) },
        },
        {
          provide: DbService,
          useValue: {
            listSchemas: () => Promise.resolve({ schemas: ['yappy'] }),
            listObjects: () => Promise.resolve({ objects: ['orders'] }),
            compile: () => Promise.resolve(BASE),
            executeSql: (req: ExecuteRequest) => {
              sent.push(req);
              return Promise.resolve({
                env: req.env,
                object_type: req.object_type,
                schema_name: req.schema_name,
                results: [],
                ok_count: 1,
                err_count: 0,
              });
            },
          },
        },
      ],
    }).compileComponents();
  });

  async function settle(fixture: ComponentFixture<CompilePage>) {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  }

  async function inScriptMode() {
    const fixture = TestBed.createComponent(CompilePage);
    const comp = fixture.componentInstance as any;
    comp.setSource('script');
    await settle(fixture);
    return { fixture, comp, el: fixture.nativeElement as HTMLElement };
  }

  it('oculta la cadena de origen: no hay Generar ni selector de objeto', async () => {
    const { el } = await inScriptMode();

    expect(el.textContent).not.toContain('Generar');
    expect(el.textContent).not.toContain('Esquema del origen');
    expect(el.textContent).not.toContain('Nombre del objeto');
    expect(el.querySelector('#compile-object')).toBeNull();
  });

  it('pide un solo ambiente: el destino', async () => {
    const { comp, el } = await inScriptMode();

    expect(comp.envB()).toBe('');
    expect(comp.envA()).toBe('');
    // Un ambiente, no dos. Se afirma sobre qué componente se renderiza y no
    // sobre el texto de una leyenda: la leyenda se fue y el comportamiento sigue.
    expect(el.querySelector('app-region-controls')).toBeNull();
    expect(el.querySelector('app-env-controls')).not.toBeNull();
  });

  it('ofrece el USE opcional, ya con el destino elegido', async () => {
    const { fixture, comp, el } = await inScriptMode();
    comp.onDestEnvChange(['qa']);
    await settle(fixture);

    expect(el.textContent).toContain('Esquema (opcional)');
    expect(el.querySelector('#compile-script-schema')).not.toBeNull();
  });

  it('ejecuta el script del usuario en el ambiente elegido', async () => {
    const { fixture, comp, el } = await inScriptMode();
    comp.onDestEnvChange(['qa']);
    comp.script.set('CREATE TABLE `pedidos` (`id` INT);');
    await settle(fixture);

    expect(comp.canExecute()).toBe(true);
    expect(el.textContent).toContain('Compilar en qa');

    comp.run();
    await settle(fixture);
    confirmarEnModal(el);
    await settle(fixture);

    expect(sent.length).toBe(1);
    expect(sent[0].env).toBe('qa');
    expect(sent[0].object_type).toBe('script');
    expect(sent[0].code).toBe('CREATE TABLE `pedidos` (`id` INT);');
    expect(sent[0].schema_name).toBe('');
  });

  it('manda el USE del esquema opcional del destino', async () => {
    const { fixture, comp } = await inScriptMode();
    comp.onDestEnvChange(['qa']);
    comp.script.set('ALTER TABLE `pedidos` ADD COLUMN `canal` VARCHAR(10);');
    // El esquema se elige después de que el destino esté puesto: `app-schema-select`
    // se vacía solo cuando cambia el ambiente.
    await settle(fixture);
    comp.schema.set('yappy');
    await settle(fixture);

    comp.run();
    await settle(fixture);
    confirmarEnModal(fixture.nativeElement as HTMLElement);
    await settle(fixture);

    expect(sent[0].schema_name).toBe('yappy');
  });

  it('no pide confirmación con el nombre del objeto cuando el origen es el script', async () => {
    const { fixture, comp, el } = await inScriptMode();
    comp.envB.set('qa'); // queda del modo ambiente
    comp.objectName.set('orders');
    comp.onDestEnvChange(['local']);
    comp.script.set('DROP PROCEDURE `proc_viejo`;');
    await settle(fixture);

    comp.run();
    await settle(fixture);

    // Sin confirmar no se ejecuta nada, y el título del modal nombra el destino
    // en vez de pedir "¿Ejecutar?" a secas.
    expect(sent).toHaveLength(0);
    expect(el.querySelector('.notice-modal')!.textContent).toContain('el script en local');

    confirmarEnModal(el);
    await settle(fixture);

    expect(sent[0].env).toBe('local');
  });

  it('descarta el script generado al cambiar el origen', async () => {
    const fixture = TestBed.createComponent(CompilePage);
    const comp = fixture.componentInstance as any;
    comp.envB.set('dev');
    comp.envA.set('local');
    comp.schema.set('yappy');
    comp.objectName.set('orders');
    comp.generate();
    await settle(fixture);
    expect(comp.script()).not.toBe('');

    comp.setSource('script');
    await settle(fixture);

    // El SQL generado describía un objeto de dev contra local: dejarlo en el
    // editor lo volvería ejecutable en otro destino sin que nadie lo pida.
    expect(comp.script()).toBe('');
    expect(comp.schema()).toBe('');
    expect(comp.result()).toBeNull();
    expect(comp.canExecute()).toBe(false);
  });

  it('no se queja de mismo ambiente cuando el destino es el único ambiente', async () => {
    const { fixture, comp, el } = await inScriptMode();
    comp.envB.set('qa');
    comp.envA.set('qa');
    await settle(fixture);

    expect(comp.sameEnv()).toBe(false);
    expect(el.textContent).not.toContain('no pueden ser el mismo ambiente');
  });

  it('sigue mandando el tipo del objeto cuando el origen es un ambiente', async () => {
    const fixture = TestBed.createComponent(CompilePage);
    const comp = fixture.componentInstance as any;
    const el = fixture.nativeElement as HTMLElement;
    comp.envB.set('dev');
    comp.envA.set('local');
    comp.objectType.set('procedure');
    // Esquema y objeto se eligen de a uno, con un flush en el medio:
    // `app-schema-select` se vacía al cambiar el ambiente, y el effect que recarga
    // la lista descarta el objeto cuando cambia el esquema.
    await settle(fixture);
    comp.schema.set('yappy');
    await settle(fixture);
    comp.objectName.set('proc_calcular');
    comp.generate();
    await settle(fixture);
    comp.script.set('DROP PROCEDURE IF EXISTS `proc_calcular`;');
    await settle(fixture);

    comp.run();
    await settle(fixture);

    expect(el.querySelector('.notice-modal')!.textContent).toContain('yappy.proc_calcular en local');

    confirmarEnModal(el);
    await settle(fixture);

    expect(sent[0].object_type).toBe('procedure');
  });

  it('la confirmación avisa que el DROP se lleva lo que haya en el destino', async () => {
    const fixture = TestBed.createComponent(CompilePage);
    const comp = fixture.componentInstance as any;
    const el = fixture.nativeElement as HTMLElement;
    comp.envB.set('dev');
    comp.envA.set('local');
    comp.script.set('DROP TABLE IF EXISTS `yappy`.`ledger`;\nCREATE TABLE `ledger` (`id` INT);');
    await settle(fixture);

    comp.run();
    await settle(fixture);

    const texto = el.querySelector('.notice-modal')!.textContent ?? '';
    expect(texto).toContain('el script borra lo que haya en local');
    expect(texto).toContain('todas sus filas');
  });

  it('no mete el aviso de DROP cuando el script no borra nada', async () => {
    const fixture = TestBed.createComponent(CompilePage);
    const comp = fixture.componentInstance as any;
    const el = fixture.nativeElement as HTMLElement;
    comp.envA.set('local');
    comp.source.set('script');
    comp.script.set('ALTER TABLE `ledger` ADD COLUMN `canal` VARCHAR(10);');
    await settle(fixture);

    comp.run();
    await settle(fixture);

    expect(el.querySelector('.notice-modal')!.textContent).not.toContain('borra lo que haya');
  });

  it('cancelar en el modal no ejecuta el script', async () => {
    const { fixture, comp, el } = await inScriptMode();
    comp.onDestEnvChange(['qa']);
    comp.script.set('DROP TABLE IF EXISTS `ledger`;');
    await settle(fixture);

    comp.run();
    await settle(fixture);
    expect(el.querySelector('.notice-modal')).not.toBeNull();

    cancelarEnModal(el);
    await settle(fixture);

    expect(sent).toHaveLength(0);
    expect(el.querySelector('.notice-modal')).toBeNull();
  });
});


describe('CompilePage — espera como modal', () => {
  let fixture: ComponentFixture<CompilePage>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [CompilePage] }).compileComponents();
    fixture = TestBed.createComponent(CompilePage);
  });

  const host = (): HTMLElement => fixture.nativeElement as HTMLElement;

  it('no abre el modal de espera en reposo', () => {
    fixture.detectChanges();
    expect(host().querySelector('.busy-modal')).toBeNull();
  });

  it('abre el modal de espera mientras la página está busy', async () => {
    (fixture.componentInstance as unknown as { busy: { set(v: boolean): void } }).busy.set(true);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(host().querySelector('.busy-modal')).not.toBeNull();
    expect(host().querySelector('.busy-modal__backdrop')).not.toBeNull();
  });

  it('cierra el modal de espera cuando deja de estar busy', async () => {
    const c = fixture.componentInstance as unknown as {
      busy: { set(v: boolean): void };
    };
    c.busy.set(true);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(host().querySelector('.busy-modal')).not.toBeNull();

    c.busy.set(false);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(host().querySelector('.busy-modal')).toBeNull();
  });
});
