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
    {
      env: 'dev',
      key: '/prod/db/url',
      ok: true,
      value: 'postgres://dev',
      error: null,
      found: true,
    },
    { env: 'qa', key: '/prod/db/url', ok: true, value: 'postgres://qa', error: null, found: true },
  ],
  ok_count: 2,
  err_count: 0,
};

describe('ParamsReadPage', () => {
  let reads: Array<[string[], string[]]>;
  let writes: any[];
  let sentSessions: any[];
  /** La señal de aborto de cada `read`, para ver si la página la aborta. */
  let readSignals: Array<AbortSignal | undefined>;
  /** Lo que devuelve el `read`. Los tests que necesitan JSON lo pisan. */
  let readResponse: any;

  beforeEach(async () => {
    reads = [];
    writes = [];
    sentSessions = [];
    readSignals = [];
    readResponse = READ_OK;

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
            read: (envs: string[], names: string[], signal?: AbortSignal) => {
              reads.push([envs, names]);
              readSignals.push(signal);
              return Promise.resolve(readResponse);
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

  async function buscar(): Promise<{
    comp: any;
    el: HTMLElement;
    fixture: ComponentFixture<ParamsReadPage>;
  }> {
    const fixture = TestBed.createComponent(ParamsReadPage);
    const comp = fixture.componentInstance as any;
    comp.envs.set(['dev', 'qa']);
    comp.name.set('/prod/db/url');
    comp.search();
    await settle(fixture);
    return { comp, el: fixture.nativeElement as HTMLElement, fixture };
  }

  /** Busca un único ambiente con el valor crudo que se le pase. */
  async function leerUnValor(raw: string) {
    readResponse = {
      env: 'dev',
      results: [
        { env: 'dev', key: '/prod/db/url', ok: true, value: raw, error: null, found: true },
      ],
      ok_count: 1,
      err_count: 0,
    };
    const fixture = TestBed.createComponent(ParamsReadPage);
    const comp = fixture.componentInstance as any;
    comp.envs.set(['dev']);
    comp.name.set('/prod/db/url');
    comp.search();
    await settle(fixture);
    return { el: fixture.nativeElement as HTMLElement, comp, panel: comp.panels()[0], fixture };
  }

  it('lee la misma clave en todos los ambientes marcados', async () => {
    await buscar();

    expect(reads).toHaveLength(1);
    expect(reads[0][0]).toEqual(['dev', 'qa']);
    expect(reads[0][1]).toEqual(['/prod/db/url']);
  });

  it('arma un panel por ambiente, con el valor de ese ambiente', async () => {
    const { el } = await buscar();

    expect(el.querySelectorAll('.env-value-panel').length).toBe(2);
    const boxes = [...el.querySelectorAll<HTMLInputElement>('.env-value-panel input')];
    expect(boxes.length).toBe(2);
    expect(boxes[0].value).toBe('postgres://dev');
    expect(boxes[1].value).toBe('postgres://qa');
  });

  describe('copiar el valor al portapapeles', () => {
    let copied: string[];

    function setClipboard(value: unknown) {
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value });
    }

    beforeEach(() => {
      copied = [];
      setClipboard({
        writeText: (text: string) => {
          copied.push(text);
          return Promise.resolve();
        },
      });
    });

    afterEach(() => {
      // jsdom no define `clipboard`: si queda el stub de este test, el siguiente
      // heredaría un portapapeles falso y probaría contra nada.
      setClipboard(undefined);
      delete (navigator as any).clipboard;
      vi.useRealTimers();
    });

    function botonesDe(el: HTMLElement): HTMLButtonElement[] {
      return [...el.querySelectorAll<HTMLButtonElement>('.env-value-panel button')];
    }

    it('copia el valor del panel del botón que se apretó', async () => {
      const { el, fixture } = await buscar();

      botonesDe(el)[1].click();
      await settle(fixture);

      // El segundo panel, no el primero.
      expect(copied).toEqual(['postgres://qa']);
    });

    it('el botón confirma que copió y solo vuelve a "Copiar"', async () => {
      const { el, fixture } = await buscar();

      // Los timers falsos van antes del click: si se instalan después, el
      // setTimeout del componente ya quedó agendado con los reales y avanzar el
      // reloj falso no lo dispara nunca.
      vi.useFakeTimers();
      botonesDe(el)[0].click();
      await Promise.resolve();
      fixture.detectChanges();
      expect(botonesDe(el)[0].textContent).toContain('Copiado');

      vi.advanceTimersByTime(2000);
      fixture.detectChanges();

      expect(botonesDe(el)[0].textContent).toContain('Copiar');
    });

    it('con JSON copia el texto indentado que se ve en pantalla', async () => {
      const { el, fixture } = await leerUnValor('{"user":"u"}');

      botonesDe(el)[0].click();
      await settle(fixture);

      // Lo que pegue el usuario tiene que ser lo que estaba leyendo, no el
      // crudo de una línea que le llegue al portapapeles.
      expect(copied).toEqual(['{\n  "user": "u"\n}']);
    });

    it('sin portapapeles avisa en vez de fingir que copió', async () => {
      delete (navigator as any).clipboard;
      const { el, comp, fixture } = await buscar();

      botonesDe(el)[0].click();
      await settle(fixture);

      expect(comp.copiedEnv()).toBeNull();
      expect(comp.error()).toContain('portapapeles');
      expect(botonesDe(el)[0].textContent).toContain('Copiar');
    });

    it('si el navegador lo niega, avisa y no marca como copiado', async () => {
      setClipboard({ writeText: () => Promise.reject(new Error('denied')) });
      const { el, comp, fixture } = await buscar();

      botonesDe(el)[0].click();
      await settle(fixture);

      expect(comp.copiedEnv()).toBeNull();
      expect(comp.error()).toContain('portapapeles');
    });

    it('un valor vacío no ofrece copiar', async () => {
      const { el } = await leerUnValor('');

      expect(botonesDe(el)[0].disabled).toBe(true);
    });
  });

  describe('el control depende de si el valor es un documento JSON', () => {
    it('un objeto JSON va en textarea, con el valor indentado', async () => {
      const { el, panel } = await leerUnValor('{"user":"u","pass":"p"}');

      expect(panel.isJson).toBe(true);
      const area = el.querySelector<HTMLTextAreaElement>('textarea.value-box');
      expect(area).not.toBeNull();
      expect(area!.value).toBe('{\n  "user": "u",\n  "pass": "p"\n}');
      // Y no queda un input en el mismo panel: son excluyentes.
      expect(el.querySelector('.env-value-panel input')).toBeNull();
    });

    it('un array JSON también va en textarea', async () => {
      const { el, panel } = await leerUnValor('["a","b"]');

      expect(panel.isJson).toBe(true);
      expect(el.querySelector('textarea.value-box')).not.toBeNull();
    });

    it('un número se queda en input aunque sea JSON válido', async () => {
      // El caso que obliga al filtro de escalar: `8401` parsea, pero no es un
      // documento, y un puerto en una caja multilínea sería unurerón.
      const { el, panel } = await leerUnValor('8401');

      expect(panel.isJson).toBe(false);
      expect(el.querySelector('textarea.value-box')).toBeNull();
      expect(el.querySelector<HTMLInputElement>('.env-value-panel input')!.value).toBe('8401');
    });

    it('otros escalares válidos se quedan en input', async () => {
      for (const raw of ['true', 'null', '"hola"']) {
        const { el, panel } = await leerUnValor(raw);
        expect(panel.isJson).toBe(false);
        expect(el.querySelector('textarea.value-box')).toBeNull();
      }
    });

    it('JSON mal formado se queda en input', async () => {
      const { el, panel } = await leerUnValor('{a: 1}');

      expect(panel.isJson).toBe(false);
      expect(el.querySelector('textarea.value-box')).toBeNull();
    });

    it('un valor vacío se queda en input', async () => {
      const { el, panel } = await leerUnValor('');

      expect(panel.isJson).toBe(false);
      expect(el.querySelector('textarea.value-box')).toBeNull();
    });

    it('la textarea crece con las líneas del JSON', async () => {
      const { el } = await leerUnValor(JSON.stringify({ a: 1, b: 2, c: 3 }));

      expect(el.querySelector<HTMLTextAreaElement>('textarea.value-box')!.rows).toBeGreaterThan(2);
    });
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

  it('la espera es un modal de fondo y no un panel en la página', () => {
    // El `busy` arranca en false: si algo lo encendiera al construir, el modal
    // taparía la pantalla entera al abrir la página.
    const fixture = TestBed.createComponent(ParamsReadPage);
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect((fixture.componentInstance as any).busy()).toBe(false);
    expect(el.querySelector('.busy-modal')).toBeNull();
  });

  it('abre el modal de espera mientras busca y lo cierra al terminar', async () => {
    // El read se resuelve por promise, así que el modal está abierto entre el
    // click y el `whenStable`: hay que mirarlo en ese medio, no sólo al final.
    const fixture = TestBed.createComponent(ParamsReadPage);
    const comp = fixture.componentInstance as any;
    comp.envs.set(['dev', 'qa']);
    comp.name.set('/prod/db/url');

    comp.search();
    fixture.detectChanges();
    expect(comp.busy()).toBe(true);
    expect((fixture.nativeElement as HTMLElement).querySelector('.busy-modal')).not.toBeNull();

    await settle(fixture);
    expect(comp.busy()).toBe(false);
    expect((fixture.nativeElement as HTMLElement).querySelector('.busy-modal')).toBeNull();
  });

  it('el modal de espera corta la búsqueda con un botón, no con el backdrop', async () => {
    const fixture = TestBed.createComponent(ParamsReadPage);
    const comp = fixture.componentInstance as any;
    comp.envs.set(['dev', 'qa']);
    comp.name.set('/prod/db/url');
    comp.search();
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    // El backdrop no cierra: la petición ya está hecha y cerrarlo con un click
    // en el fondo mentiría sobre lo que está pasando. El botón es el que corta.
    const backdrop = el.querySelector('.busy-modal__backdrop')!;
    expect(backdrop.tagName).toBe('DIV');
    expect(el.querySelector('.busy-modal__x')).toBeNull();

    // Y el texto se anuncia por aria-live en vez de mover el foco.
    expect(el.querySelector('.busy-modal')?.getAttribute('role')).toBe('status');

    await settle(fixture);
  });

  it('el botón de cancelar aborta la búsqueda y no muestra error', async () => {
    // El `read` del mock ignora la señal, así que la promesa resuelve igual: lo
    // que se afirma es que el abort corta la operación y que una respuesta que
    // llega después no escribe nada.
    const fixture = TestBed.createComponent(ParamsReadPage);
    const comp = fixture.componentInstance as any;
    comp.envs.set(['dev', 'qa']);
    comp.name.set('/prod/db/url');
    comp.search();
    fixture.detectChanges();

    (fixture.nativeElement as HTMLElement)
      .querySelector<HTMLButtonElement>('.busy-modal__cancel')!
      .click();
    fixture.detectChanges();

    // Cortar la espera es lo que pidió el usuario: no es un fallo y no hay caja
    // de error. Los paneles de la búsqueda anterior siguen como estaban.
    expect(comp.busy()).toBe(false);
    expect(comp.error()).toBeNull();
    expect((fixture.nativeElement as HTMLElement).querySelector('.busy-modal')).toBeNull();

    // Y la señal que viajó por el servicio quedó abortada: eso es lo que
    // corta el fetch. Ver `core/cancel`.
    expect(readSignals[0]?.aborted).toBe(true);

    await settle(fixture);
    // La respuesta que llegó tarde no arma paneles nuevos.
    expect(comp.panels().length).toBe(0);
  });
});
