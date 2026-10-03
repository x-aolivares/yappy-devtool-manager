/**
 * Red de seguridad mínima de Leer Parámetros, escrita **antes** de restilarla.
 *
 * `params-read.ts` no tenía un solo test y el rediseño la movía entero. Estos
 * tests cubren el andamiaje y los caminos que un refactor de template puede
 * romper —qué se manda al servicio, cómo se arma un panel por ambiente, cuándo
 * se habilita la escritura— y se escribieron contra el componente **sin tocar**.
 *
 * Afirman sobre el mecanismo, nunca sobre el texto de una leyenda: un assert de
 * caption se rompe cada vez que se limpia una línea de copy.
 */
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ParamsReadPage } from './params-read';
import { DbService } from '../core/services/db.service';
import { EnvironmentService } from '../core/services/environment.service';
import { ParamsService } from '../core/services/params.service';
import { SessionService } from '../core/services/session.service';

const READ_OK: Record<string, any> = {
  env: 'dev',
  results: [
    { env: 'dev', key: '/prod/db/url', ok: true, value: 'postgres://dev', error: null, found: true },
    { env: 'qa', key: '/prod/db/url', ok: true, value: 'postgres://qa', error: null, found: true },
  ],
  ok_count: 2,
  err_count: 0,
};

describe('ParamsReadPage', () => {
  let reads: Array<[string[], string[]]>;
  let writes: any[];
  let sentSessions: any[];

  beforeEach(async () => {
    reads = [];
    writes = [];
    sentSessions = [];

    await TestBed.configureTestingModule({
      imports: [ParamsReadPage],
      providers: [
        provideRouter([]),
        {
          provide: EnvironmentService,
          useValue: { list: () => Promise.resolve({ environments: [] }) },
        },
        {
          provide: DbService,
          useValue: {},
        },
        {
          provide: ParamsService,
          useValue: {
            read: (envs: string[], names: string[]) => {
              reads.push([envs, names]);
              return Promise.resolve(READ_OK);
            },
            multi: (req: any) => {
              writes.push(req);
              return Promise.resolve({
                env_b: 'dev',
                env_a: 'dev',
                dry_run: false,
                results: [{ env: req.envs?.[0] ?? 'dev', ok: true, error: null }],
                notes: [],
                ok_count: 1,
                err_count: 0,
              });
            },
          },
        },
        {
          provide: SessionService,
          useValue: {
            create: (req: any) => {
              sentSessions.push(req);
              return Promise.resolve({ id: '7', title: 'release/REP-1' });
            },
          },
        },
      ],
    }).compileComponents();
  });

  async function settle(fixture: ComponentFixture<ParamsReadPage>): Promise<void> {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  }

  async function buscar(): Promise<{ comp: any; el: HTMLElement; fixture: ComponentFixture<ParamsReadPage> }> {
    const fixture = TestBed.createComponent(ParamsReadPage);
    const comp = fixture.componentInstance as any;
    comp.envs.set(['dev', 'qa']);
    comp.name.set('/prod/db/url');
    comp.search();
    await settle(fixture);
    return { comp, el: fixture.nativeElement as HTMLElement, fixture };
  }

  it('lee la misma clave en todos los ambientes marcados', async () => {
    await buscar();

    expect(reads).toHaveLength(1);
    expect(reads[0][0]).toEqual(['dev', 'qa']);
    expect(reads[0][1]).toEqual(['/prod/db/url']);
  });

  it('arma un panel por ambiente, con el valor de ese ambiente', async () => {
    const { comp, el } = await buscar();

    expect(comp.panels().length).toBe(2);
    const boxes = [...el.querySelectorAll<HTMLTextAreaElement>('textarea.value-box')];
    expect(boxes.length).toBe(2);
    expect(boxes[0].value).toBe('postgres://dev');
    expect(boxes[1].value).toBe('postgres://qa');
  });

  it('el botón de escribir existe uno por panel', async () => {
    const { el } = await buscar();

    expect(el.querySelectorAll('button.update-btn').length).toBe(2);
  });

  it('Actualizar escribe SÓLO el ambiente de ese panel', async () => {
    const { comp, el, fixture } = await buscar();

    // `update()` pasa por `window.confirm`, que en el entorno de test devuelve
    // false: sin esto el método corta antes de escribir y el test probaría nada.
    const spy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const botones = [...el.querySelectorAll<HTMLButtonElement>('button.update-btn')];
    botones[1].click();
    await settle(fixture);
    spy.mockRestore();

    // El ambiente enviado es el del panel que se apretó, no el primero.
    expect(writes).toHaveLength(1);
    expect(writes[0].envs).toEqual(['qa']);
    expect(writes[0].confirm).toBe(true);
    // Y queda el resultado de esa escritura, no de otra.
    expect(comp.writeStatus()['qa']?.ok).toBe(true);
  });

  it('Actualizar sin confirmar no escribe nada', async () => {
    const { el, fixture } = await buscar();

    const spy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const botones = [...el.querySelectorAll<HTMLButtonElement>('button.update-btn')];
    botones[1].click();
    await settle(fixture);
    spy.mockRestore();

    expect(writes).toHaveLength(0);
  });

  it('editar el valor de un panel no toca el otro', async () => {
    const { comp } = await buscar();

    comp.onEdit(comp.panels()[0], 'postgres://dev-editado');

    expect(comp.panels()[0].value).toBe('postgres://dev-editado');
    expect(comp.panels()[1].value).toBe('postgres://qa');
  });

  it('con dos ambientes arma la sesión de trabajo con la clave buscada', async () => {
    await buscar();

    expect(sentSessions.length).toBe(1);
    expect(sentSessions[0].keys).toEqual(['/prod/db/url']);
    expect(sentSessions[0].env_b).toBe('dev');
    expect(sentSessions[0].env_a).toBe('qa');
  });
});
