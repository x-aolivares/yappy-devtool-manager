import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { CompilePage } from './compile';
import { DbService } from '../core/services/db.service';
import { EnvironmentService } from '../core/services/environment.service';

const BASE: Record<string, any> = {
  env_a: 'local',
  env_b: 'dev',
  object_type: 'table',
  schema_name: 'yappy',
  object_name: 'orders',
  status: 'equal',
  code_a: 'CREATE TABLE `orders` (`id` INT)',
  code_b: 'CREATE TABLE `orders` (`id` INT, `newcol` INT)',
  script: null,
  notes: [],
};

describe('CompilePage prefill del editor', () => {
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
      status: 'different',
      script: 'ALTER TABLE `orders` ADD COLUMN `newcol` INT;',
    });
    expect(comp.script()).toBe('ALTER TABLE `orders` ADD COLUMN `newcol` INT;');
  });

  it('cae a code_b cuando script es null (destino ya igual)', async () => {
    const { comp } = await generate({ status: 'equal', script: null });
    expect(comp.script()).toBe('CREATE TABLE `orders` (`id` INT, `newcol` INT)');
  });

  it('cae a code_b cuando script es string vacío (DDL difiere, estructura igual)', async () => {
    const { comp } = await generate({ status: 'different', script: '' });
    expect(comp.script()).toBe('CREATE TABLE `orders` (`id` INT, `newcol` INT)');
  });

  it('deja el editor vacío cuando no hay script ni code_b', async () => {
    const { comp } = await generate({ status: 'none', code_b: null, code_a: null });
    expect(comp.script()).toBe('');
  });

  it('deja el botón Compilar habilitado con el contenido sembrado', async () => {
    const { comp, el } = await generate({ status: 'equal', script: null });
    expect(comp.canExecute()).toBe(true);
    const run = Array.from(el.querySelectorAll('button')).find((b) =>
      (b.textContent ?? '').includes('Compilar en'),
    ) as HTMLButtonElement;
    expect(run.disabled).toBe(false);
  });

  it('avisa que no hay nada que aplicar y que corría sin editar falla', async () => {
    const { comp, el } = await generate({ status: 'equal', script: null });
    expect(comp.prefillNotice()).toContain('ya es idéntico al origen');
    expect(comp.prefillNotice()).toContain('va a fallar');
    expect(el.textContent).toContain('editala con el cambio que quieras llevar');
  });

  it('no avisa cuando hay un script que aplicar', async () => {
    const { comp } = await generate({
      status: 'different',
      script: 'ALTER TABLE `orders` ADD COLUMN `newcol` INT;',
    });
    expect(comp.prefillNotice()).toBeNull();
  });

  it('descarta el contenido sembrado cuando cambia un selector', async () => {
    const { fixture, comp } = await generate({ status: 'equal', script: null });
    expect(comp.script()).not.toBe('');

    comp.objectName.set('other');
    fixture.detectChanges();

    expect(comp.script()).toBe('');
    expect(comp.canExecute()).toBe(false);
  });
});
