/**
 * La pantalla de Workflow/Recursos.
 *
 * Lo que se verifica acá es que la pantalla diga la verdad sobre el estado, no
 * que se vea bien. Tres cosas que sólo se ven mirando el comportamiento:
 *
 * - **Una fila `starting` no ofrece "Levantar".** El proceso ya está en marcha y
 *   un segundo click abre un `start-session` más sobre un puerto que ya está
 *   bindeado.
 * - **Una fila ajena no tiene botón de parar.** `kill_ssm()` trabaja sobre el
 *   tracker y no le importa quién levantó qué, así que el botón de una fila es la
 *   única barrera entre un clic y el túnel de otro proyecto.
 * - **El cambio de estado se anuncia en un `aria-live`.** Todo en esta pantalla
 *   cambia de forma asíncrona y sin mover el foco: sin esto, quien no ve el punto
 *   de color no tiene ninguna forma de saber que el túnel quedó arriba.
 */
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { WorkflowResourcesPage } from './workflow-resources';
import { WorkflowService } from '../../core/services/workflow.service';
import { EnvironmentService } from '../../core/services/environment.service';
import type { WorkflowStateResponse } from '../../api-gen/models';

function fila(over: Partial<WorkflowStateResponse['ports'][number]> = {}) {
  return {
    cluster: 'cap',
    port: 8402,
    label: 'core-p2m-m2p',
    state: 'down',
    pid: null,
    since: null,
    foreign: false,
    browser_url: null,
    log_file: '',
    conflict: null,
    ...over,
  };
}

function recurso(over: Partial<WorkflowStateResponse['resources'][number]> = {}) {
  return {
    key: 'bastion',
    group: 'named',
    name: 'Bastion',
    detail: 'Documento distinto',
    state: 'down',
    remote_port: 9091,
    local_port: 3000,
    pid: null,
    since: null,
    foreign: false,
    browser_url: null,
    log_file: '',
    conflict: null,
    ...over,
  };
}

function estado(over: Partial<WorkflowStateResponse> = {}): WorkflowStateResponse {
  return {
    cluster: 'cap',
    env: 'dev',
    generated_at: 1757000000,
    remote_host: 'nlbcapabilities.dev.yappycloud.com',
    resources: [],
    ports: [],
    unattributed: [],
    ...over,
  };
}

const ENVS = [
  { env: 'dev', region: 'us-west-2', profile: 'base-profile' },
  { env: 'qa', region: 'us-west-1', profile: 'base-profile' },
];

describe('WorkflowResourcesPage', () => {
  let answer: WorkflowStateResponse;
  let startedPorts: Array<{ port: number; label: string; cluster: string }>;
  let startedKeys: string[];
  let stopped: Array<{ pid: number; allowForeign: boolean }>;
  let stopAllCalls: boolean[];
  let renames: Array<{ cluster: string; port: number; label: string }>;
  let removed: Array<{ cluster: string; port: number }>;
  let failStart: ((err: Error) => void) | null;

  beforeEach(async () => {
    answer = estado();
    startedPorts = [];
    startedKeys = [];
    stopped = [];
    stopAllCalls = [];
    renames = [];
    removed = [];
    failStart = null;

    await TestBed.configureTestingModule({
      imports: [WorkflowResourcesPage],
      providers: [
        {
          provide: WorkflowService,
          useValue: {
            state: () => Promise.resolve(answer),
            log: () => Promise.resolve({ pid: 1, text: '', offset: 0, alive: true }),
            startResource: (key: string) => {
              startedKeys.push(key);
              return failStart
                ? Promise.reject(failStart(new Error('El puerto local 8403 ya lo está usando el cluster cap2')))
                : Promise.resolve({ ok: true });
            },
            startPort: (port: number, label: string, _env: string, cluster: string) => {
              startedPorts.push({ port, label, cluster });
              return failStart ? Promise.reject(failStart(new Error('rechazado'))) : Promise.resolve({ ok: true });
            },
            stop: (pid: number, allowForeign = false) => {
              stopped.push({ pid, allowForeign });
              return Promise.resolve({ ok: true, stopped: [pid] });
            },
            stopAll: (allowForeign = false) => {
              stopAllCalls.push(allowForeign);
              return Promise.resolve({ ok: true, stopped: [1], refused: [] });
            },
            addPort: (port: number) => Promise.resolve({ ok: true, port }),
            renamePort: (cluster: string, port: number, label: string) => {
              renames.push({ cluster, port, label });
              return Promise.resolve({ cluster, ports: [] });
            },
            removePort: (cluster: string, port: number) => {
              removed.push({ cluster, port });
              return Promise.resolve({ cluster, ports: [] });
            },
          },
        },
        {
          provide: EnvironmentService,
          useValue: { list: () => Promise.resolve({ environments: ENVS }) },
        },
      ],
    }).compileComponents();
  });

  async function montar(): Promise<{
    comp: WorkflowResourcesPage;
    el: HTMLElement;
    fixture: ComponentFixture<WorkflowResourcesPage>;
  }> {
    const fixture = TestBed.createComponent(WorkflowResourcesPage);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return {
      comp: fixture.componentInstance,
      el: fixture.nativeElement as HTMLElement,
      fixture,
    };
  }

  /** El texto de una tarjeta, por el nombre del recurso. */
  function tarjeta(el: HTMLElement, nombre: string): HTMLElement {
    const card = [...el.querySelectorAll<HTMLElement>('.res-card')].find((c) =>
      c.querySelector('.res-card__name')?.textContent?.includes(nombre),
    );
    if (!card) throw new Error(`No está la tarjeta de ${nombre}`);
    return card;
  }

  function botones(card: HTMLElement): string[] {
    return [...card.querySelectorAll('button')].map((b) => b.textContent?.trim() ?? '');
  }

  // --- qué ofrece cada estado ----------------------------------------------

  describe('qué ofrece cada estado', () => {
    it('una fila abajo ofrece levantar y nada más', async () => {
      answer = estado({ ports: [fila()] });
      const { el } = await montar();

      const row = el.querySelector('.port-table tbody tr') as HTMLElement;
      expect(botones(row)).toContain('Levantar');
      expect(botones(row)).not.toContain('Parar');
      expect(row.textContent).toContain('abajo');
    });

    it('una fila levantándose NO ofrece levantar de nuevo', async () => {
      // El proceso ya está en marcha. Un segundo click abre otro start-session
      // sobre un puerto que ya está bindeado.
      answer = estado({ ports: [fila({ state: 'starting' })] });
      const { el } = await montar();

      const row = el.querySelector('.port-table tbody tr') as HTMLElement;
      const labels = botones(row);
      expect(labels).toContain('Levantando…');
      expect(labels.filter((l) => l === 'Levantar')).toHaveLength(0);
    });

    it('una fila arriba ofrece parar y abrir, y no levantar', async () => {
      answer = estado({ ports: [fila({ state: 'up', pid: 4242, browser_url: 'https://localhost:8402/swagger-ui/index.html' })] });
      const { el } = await montar();

      const row = el.querySelector('.port-table tbody tr') as HTMLElement;
      expect(botones(row)).toContain('Parar');
      expect(botones(row).filter((l) => l === 'Levantar')).toHaveLength(0);
      expect(row.querySelector('a[href*="swagger-ui"]')).not.toBeNull();
    });

    it('una fila caída dice "Levantar de nuevo", no "Levantar"', async () => {
      answer = estado({ ports: [fila({ state: 'dead' })] });
      const { el } = await montar();

      const labels = botones(el.querySelector('.port-table tbody tr') as HTMLElement);
      expect(labels).toContain('Levantar de nuevo');
      expect(labels).not.toContain('Levantar');
    });

    it('una fila en error ofrece reintentar y cerrar', async () => {
      answer = estado({ ports: [fila({ state: 'error' })] });
      const { el } = await montar();

      expect(botones(el.querySelector('.port-table tbody tr') as HTMLElement)).toContain('Reintentar');
    });
  });

  // --- los procesos ajenos --------------------------------------------------

  describe('los procesos ajenos', () => {
    it('llevan borde punteado y la etiqueta "ajeno"', async () => {
      answer = estado({ resources: [recurso({ foreign: true, state: 'up', pid: 99 })] });
      const { el } = await montar();

      const card = tarjeta(el, 'Bastion');
      expect(card.classList).toContain('res-card--foreign');
      expect(card.textContent).toContain('ajeno');
    });

    it('una fila ajena NO tiene botón de parar', async () => {
      // El botón de una fila nunca alcanza un proceso que no es de la web: es la
      // única barrera entre un clic y el túnel de otro proyecto.
      answer = estado({ resources: [recurso({ foreign: true, state: 'up', pid: 99 })] });
      const { el } = await montar();

      expect(botones(tarjeta(el, 'Bastion'))).not.toContain('Parar');
    });

    it('parar desde la fila manda el pid sin allow_foreign', async () => {
      answer = estado({ resources: [recurso({ state: 'up', pid: 77 })] });
      const { comp } = await montar();

      await comp.stopOne({ key: 'bastion', group: 'named', name: 'Bastion', detail: '', state: 'up', pid: 77, since: null, foreign: false, browser_url: null, log_file: '', conflict: null });
      await new Promise((r) => setTimeout(r, 0));

      expect(stopped).toEqual([{ pid: 77, allowForeign: false }]);
    });

    it('"Parar todo" es el único que pide confirmación', async () => {
      answer = estado({ resources: [recurso({ state: 'up', pid: 77 })] });
      const { comp, el, fixture } = await montar();

      expect(el.querySelector('app-notice-modal')).toBeNull();
      comp.askStopAll();
      fixture.detectChanges();

      expect(el.querySelector('app-notice-modal')).not.toBeNull();
    });

    it('el aviso de "Parar todo" dice cuántos ajenos alcanza', async () => {
      // El número viene del servidor, no se estima en el navegador: el aviso es
      // lo único que separa "cierra lo que ves" de "cierra también lo de otro
      // proyecto".
      answer = estado({
        resources: [
          recurso({ key: 'bastion', foreign: true, state: 'up', pid: 99 }),
          recurso({ key: 'kafka-ui', group: 'kafka', name: 'Kafdrop UI', foreign: true, state: 'up', pid: 98 }),
          recurso({ key: 'db', group: 'db', name: 'Tunnel Aurora', state: 'up', pid: 97 }),
        ],
      });
      const { comp } = await montar();

      comp.askStopAll();

      expect(comp.foreignCount()).toBe(2);
      const aviso = comp.notice();
      expect(aviso?.paragraphs.join(' ')).toContain('2 procesos ajenos');
    });

    it('confirmar "Parar todo" sí incluye los ajenos', async () => {
      answer = estado({ resources: [recurso({ foreign: true, state: 'up', pid: 99 })] });
      const { comp } = await montar();

      comp.askStopAll();
      await comp.confirmStopAll();
      await new Promise((r) => setTimeout(r, 0));

      expect(stopAllCalls).toEqual([true]);
    });
  });

  // --- el aria-live ---------------------------------------------------------

  describe('el anuncio de cambios', () => {
    it('el live region está siempre en el DOM, aunque esté vacío', async () => {
      // Si se insertara junto al contenido que cambia, el lector lo anunciaría
      // como texto nuevo. Tiene que existir antes.
      const { el } = await montar();

      const live = el.querySelector('[aria-live="polite"]');
      expect(live).not.toBeNull();
      expect(live?.getAttribute('role')).toBe('status');
    });

    it('no anuncia nada en el primer render', async () => {
      // El primer render no es un cambio: anunciarlo sería leer toda la pantalla
      // en voz alta de golpe.
      answer = estado({ ports: [fila({ state: 'up' })] });
      const { comp } = await montar();

      expect(comp.announcement()).toBe('');
    });

    it('anuncia una fila que sube', async () => {
      answer = estado({ ports: [fila({ state: 'down' })] });
      const { comp, fixture } = await montar();

      answer = estado({ ports: [fila({ state: 'up' })] });
      comp.state.reload();
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(comp.announcement()).toContain('arriba');
    });

    it('usa el puerto cuando la fila no tiene nombre', async () => {
      answer = estado({ ports: [fila({ state: 'down', label: '' })] });
      const { comp, fixture } = await montar();

      answer = estado({ ports: [fila({ state: 'up', label: '' })] });
      comp.state.reload();
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(comp.announcement()).toContain(':8402');
    });
  });

  // --- arrancar -------------------------------------------------------------

  describe('arrancar', () => {
    it('un start rechazado va al modal, no a la fila', async () => {
      // "Otro cluster tiene el 8403" no es un error de esta fila: ponerlo en la
      // fila mandaría al developer a la cosa equivocada. El motivo del backend
      // viaja entero y el modal lo muestra.
      answer = estado({ ports: [fila()] });
      const { comp, fixture } = await montar();
      failStart = (err) => err;

      await comp.startPort(fila());
      await new Promise((r) => setTimeout(r, 0));
      fixture.detectChanges();

      expect(comp.notice()?.title).toBe('No se pudo levantar el túnel');
      expect(comp.notice()?.paragraphs.join(' ')).toContain('rechazado');
    });

    it('"Levantar todos" manda el cluster de la pantalla', async () => {
      answer = estado({ ports: [fila({ port: 8402 }), fila({ port: 8403, label: 'commerce' })] });
      const { comp } = await montar();

      await comp.startAllDown();
      await new Promise((r) => setTimeout(r, 0));

      expect(startedPorts).toEqual([
        { port: 8402, label: 'core-p2m-m2p', cluster: 'cap' },
        { port: 8403, label: 'commerce', cluster: 'cap' },
      ]);
    });

    it('"Levantar todos" se saltea las caídas', async () => {
      // El botón multiple no tiene dónde decir "esto se cayó". Para eso está el
      // botón de su fila, que sí lo dice.
      answer = estado({
        ports: [fila({ port: 8402, state: 'down' }), fila({ port: 8403, state: 'dead' })],
      });
      const { comp } = await montar();

      await comp.startAllDown();
      await new Promise((r) => setTimeout(r, 0));

      expect(startedPorts.map((s) => s.port)).toEqual([8402]);
    });
  });

  // --- el catálogo ----------------------------------------------------------

  describe('el catálogo', () => {
    it('rechaza un puerto fuera de rango antes de llamar al backend', async () => {
      const { comp } = await montar();

      comp.addPortValue.set('70000');
      await comp.addPort();

      expect(comp.addPortError()).toContain('entre 1 y 65535');
      expect(startedPorts).toHaveLength(0);
    });

    it('pide el número si está vacío', async () => {
      const { comp } = await montar();

      comp.addPortValue.set('  ');
      await comp.addPort();

      expect(comp.addPortError()).toBe('Falta el número de puerto.');
    });

    it('el contador distingue puertos de puertos con nombre', async () => {
      answer = estado({
        ports: [fila({ port: 8402, label: 'core' }), fila({ port: 8403, label: '' })],
      });
      const { comp, el } = await montar();

      expect(comp.catalogSummary()).toBe('2 puertos · 1 con nombre');
      expect(el.textContent).toContain('2 puertos · 1 con nombre');
    });

    it('una fila con conflicto lo dice y no ofrece pararla', async () => {
      answer = estado({ ports: [fila({ conflict: 'el cluster cap2' })] });
      const { el } = await montar();

      const row = el.querySelector('.port-table tbody tr') as HTMLElement;
      expect(row.textContent).toContain('ya lo está usando el cluster cap2');
      expect(row.getAttribute('data-conflict')).toBe('true');
    });

    it('quitar la fila de un túnel arriba lo para antes', async () => {
      // La fila que se borra es lo único que puede parar el túnel.
      answer = estado({ ports: [fila({ state: 'up', pid: 5150 })] });
      const { comp } = await montar();

      await comp.removePort(fila({ state: 'up', pid: 5150 }));
      await new Promise((r) => setTimeout(r, 0));

      expect(stopped).toEqual([{ pid: 5150, allowForeign: false }]);
      expect(removed).toEqual([{ cluster: 'cap', port: 8402 }]);
    });

    it('quitar una fila apagada no intenta parar nada', async () => {
      answer = estado({ ports: [fila()] });
      const { comp } = await montar();

      await comp.removePort(fila());
      await new Promise((r) => setTimeout(r, 0));

      expect(stopped).toHaveLength(0);
      expect(removed).toEqual([{ cluster: 'cap', port: 8402 }]);
    });
  });

  // --- el guardado del nombre ----------------------------------------------

  describe('guardar el nombre del servicio', () => {
    it('no escribe en cada tecla', async () => {
      const { comp } = await montar();
      const row = fila();

      comp.writeLabel(row, 'c');
      comp.writeLabel(row, 'co');
      comp.writeLabel(row, 'commerce');
      expect(renames).toHaveLength(0);

      await new Promise((r) => setTimeout(r, 600));
      // Una sola escritura, no tres. El catálogo es `CLUSTER_PORTS` en
      // `env.base`: escribir en cada tecla sería escribir el archivo del
      // developer treinta veces por palabra.
      expect(renames).toEqual([{ cluster: 'cap', port: 8402, label: 'commerce' }]);
    });

    it('no reescribe si el nombre no cambió', async () => {
      const { comp } = await montar();

      comp.writeLabel(fila({ label: 'core' }), 'core');
      await new Promise((r) => setTimeout(r, 600));

      expect(renames).toHaveLength(0);
    });

    it('cada fila tiene su propio debounce', async () => {
      const { comp } = await montar();

      comp.writeLabel(fila({ port: 8402 }), 'core');
      comp.writeLabel(fila({ port: 8403 }), 'commerce');
      await new Promise((r) => setTimeout(r, 600));

      expect(renames.map((r) => r.port)).toEqual([8402, 8403]);
    });
  });

  // --- el resumen -----------------------------------------------------------

  describe('la barra de resumen', () => {
    it('cuenta arriba, levantando y con problemas', async () => {
      answer = estado({
        ports: [
          fila({ port: 8402, state: 'up', pid: 1 }),
          fila({ port: 8403, state: 'starting', pid: 2 }),
          fila({ port: 8404, state: 'error', pid: 3 }),
        ],
      });
      const { comp } = await montar();

      expect(comp.summary()).toBe('1 arriba · 1 levantando · 1 con problemas');
    });

    it('acusa los levantados fuera de la web', async () => {
      answer = estado({ ports: [fila({ state: 'up', pid: 1, foreign: true })] });
      const { comp } = await montar();

      expect(comp.summary()).toContain('1 levantado fuera de la web');
    });

    it('"Parar todo" se deshabilita cuando no hay nada vivo', async () => {
      const { comp, el } = await montar();

      expect(comp.anythingAlive()).toBe(false);
      const stopAll = [...el.querySelectorAll('button')].find((b) => b.textContent?.includes('Parar todo'));
      expect(stopAll?.disabled).toBe(true);
    });
  });

  // --- errores --------------------------------------------------------------

  describe('cuando el backend falla', () => {
    it('el error del poll dice qué pasó', async () => {
      const { comp } = await montar();

      expect(comp.describePollFailureForTest(new Error('boom'))).toBe('boom');
      // Sin conexión es el caso que más se da: el server se apagó o el túnel de
      // la sesión se cayó. Un "status 0" a secas no dice nada.
      expect(comp.describePollFailureForTest(new HttpErrorResponse({ status: 0 }))).toContain(
        'No se pudo conectar',
      );
      expect(
        comp.describePollFailureForTest(
          new HttpErrorResponse({ status: 500, error: { detail: 'se rompió' } }),
        ),
      ).toBe('se rompió');
    });

    it('la caja no aparece si el poll trajo datos', async () => {
      const { comp, fixture } = await montar();

      // La vuelta terminó bien: el `error()` del `resource()` es `undefined`.
      comp.settleFailureForTest(undefined);
      fixture.detectChanges();

      expect(comp.readError()).toBe('');
      expect(fixture.nativeElement.querySelector('.error-box')).toBeNull();
    });

    it('la caja se va sola cuando el servidor vuelve', async () => {
      // El bug: leer `state.error()` del `resource()` dejaba la caja pegada para
      // siempre. Apagar el server con Ctrl+C deja el poll golpeando un backend
      // muerto, cada tick falla, y el último error sobrevive a la recuperación —
      // diciendo que no se puede leer algo que hace un rato se está leyendo.
      const { comp, fixture } = await montar();

      // El server está caído: el poll falla.
      comp.settleFailureForTest(new HttpErrorResponse({ status: 0 }));
      fixture.detectChanges();

      expect(comp.readError()).toContain('No se pudo conectar');
      expect(fixture.nativeElement.querySelector('.error-box')).not.toBeNull();

      // El server volvió y el poll trajo datos otra vez.
      comp.settleFailureForTest(undefined);
      fixture.detectChanges();

      expect(comp.readError()).toBe('');
      expect(fixture.nativeElement.querySelector('.error-box')).toBeNull();
    });
  });
});
