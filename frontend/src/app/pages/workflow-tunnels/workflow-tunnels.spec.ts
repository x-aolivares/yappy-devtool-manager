/**
 * Túneles de cluster.
 *
 * Es la página que más superficie tiene de las cuatro: el catálogo se edita y se
 * guarda, el cluster se elige, hay un botón múltiple y hay una sección condicional.
 * Lo que se verifica es que la pantalla diga la verdad sobre el estado, no que se
 * vea bien. Cuatro cosas que sólo se ven mirando el comportamiento:
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
 * - **El aviso de "Parar todo" cuenta sobre el tracker entero**, no sobre las
 *   filas de esta página —ver `stopAllForeignCount`.
 */
import { TestBed } from '@angular/core/testing';

import { WorkflowTunnelsPage } from './workflow-tunnels';
import {
  WorkflowCalls, callsVacios, emiteCaida, emiteEstado, fila, recurso, workflowProviders,
} from '../../core/workflow-doubles';

describe('WorkflowTunnelsPage', () => {
  let calls: WorkflowCalls;

  beforeEach(async () => {
    calls = callsVacios();
    await TestBed.configureTestingModule({
      imports: [WorkflowTunnelsPage],
      providers: workflowProviders(calls),
    }).compileComponents();
  });

  async function montar() {
    const fixture = TestBed.createComponent(WorkflowTunnelsPage);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return {
      comp: fixture.componentInstance,
      el: fixture.nativeElement as HTMLElement,
      fixture,
    };
  }

  /** Cambia la respuesta del endpoint y pide una vuelta, como si pasara el poll. */
  async function recargar(fixture: { detectChanges(): void }): Promise<void> {
    fixture.detectChanges();
    await new Promise((r) => setTimeout(r, 0));
    fixture.detectChanges();
  }

  function filaDeTabla(el: HTMLElement, indice = 0): HTMLElement {
    return el.querySelectorAll('.port-table tbody tr')[indice] as HTMLElement;
  }

  function botones(card: HTMLElement): string[] {
    return [...card.querySelectorAll('button')].map((b) => b.textContent?.trim() ?? '');
  }

  // --- qué ofrece cada estado ----------------------------------------------

  describe('qué ofrece cada estado', () => {
    it('una fila abajo ofrece levantar y nada más', async () => {
      calls.answer = { ...calls.answer, ports: [fila()] };
      const { el } = await montar();

      const row = filaDeTabla(el);
      expect(botones(row)).toContain('Levantar');
      expect(botones(row)).not.toContain('Parar');
      expect(row.textContent).toContain('abajo');
    });

    it('una fila levantándose NO ofrece levantar de nuevo', async () => {
      // El proceso ya está en marcha. Un segundo click abre otro start-session
      // sobre un puerto que ya está bindeado.
      calls.answer = { ...calls.answer, ports: [fila({ state: 'starting' })] };
      const { el } = await montar();

      const labels = botones(filaDeTabla(el));
      expect(labels).toContain('Levantando…');
      expect(labels.filter((l) => l === 'Levantar')).toHaveLength(0);
    });

    it('una fila arriba ofrece parar y abrir, y no levantar', async () => {
      calls.answer = {
        ...calls.answer,
        ports: [fila({ state: 'up', pid: 4242, browser_url: 'https://localhost:8402/swagger-ui/index.html' })],
      };
      const { el } = await montar();

      const row = filaDeTabla(el);
      expect(botones(row)).toContain('Parar');
      expect(botones(row).filter((l) => l === 'Levantar')).toHaveLength(0);
      expect(row.querySelector('a[href*="swagger-ui"]')).not.toBeNull();
    });

    it('una fila caída dice "Levantar de nuevo", no "Levantar"', async () => {
      calls.answer = { ...calls.answer, ports: [fila({ state: 'dead' })] };
      const { el } = await montar();

      const labels = botones(filaDeTabla(el));
      expect(labels).toContain('Levantar de nuevo');
      expect(labels).not.toContain('Levantar');
    });

    it('una fila en error ofrece reintentar', async () => {
      calls.answer = { ...calls.answer, ports: [fila({ state: 'error' })] };
      const { el } = await montar();

      expect(botones(filaDeTabla(el))).toContain('Reintentar');
    });
  });

  // --- los procesos ajenos --------------------------------------------------

  describe('los procesos ajenos', () => {
    it('una fila ajena NO tiene botón de parar', async () => {
      // El botón de una fila nunca alcanza un proceso que no es de la web: es la
      // única barrera entre un clic y el túnel de otro proyecto.
      calls.answer = { ...calls.answer, ports: [fila({ foreign: true, state: 'up', pid: 99 })] };
      const { el } = await montar();

      expect(botones(filaDeTabla(el))).not.toContain('Parar');
    });

    it('parar desde la fila manda el pid sin allow_foreign', async () => {
      calls.answer = { ...calls.answer, ports: [fila({ state: 'up', pid: 77 })] };
      const { comp } = await montar();

      await comp.stopOne(fila({ state: 'up', pid: 77 }));
      await new Promise((r) => setTimeout(r, 0));

      expect(calls.stopped).toEqual([{ pid: 77, allowForeign: false }]);
    });

    it('"Parar todo" es el único que pide confirmación', async () => {
      calls.answer = { ...calls.answer, ports: [fila({ state: 'up', pid: 77 })] };
      const { comp, el, fixture } = await montar();

      expect(el.querySelector('app-notice-modal')).toBeNull();
      comp.askStopAll();
      fixture.detectChanges();

      expect(el.querySelector('app-notice-modal')).not.toBeNull();
    });

    it('el aviso de "Parar todo" dice cuántos ajenos alcanza, contando todo el tracker', async () => {
      // El número viene del servidor, no se estima en el navegador: el aviso es
      // lo único que separa "cierra lo que ves" de "cierra también lo de otro
      // proyecto".
      //
      // Y cuenta **todo el tracker**, no las filas de esta página: los dos ajenos
      // de Kafka están en otra sección, y si el aviso dijera "0" el developer
      // confirmaría pensando que no hay riesgo.
      calls.answer = {
        ...calls.answer,
        ports: [],
        resources: [
          recurso({ key: 'bastion', foreign: true, state: 'up', pid: 99 }),
          recurso({ key: 'kafka-ui', group: 'kafka', name: 'Kafdrop UI', foreign: true, state: 'up', pid: 98 }),
          recurso({ key: 'db', group: 'db', name: 'Tunnel Aurora', state: 'up', pid: 97 }),
        ],
      };
      const { comp } = await montar();

      comp.askStopAll();

      expect(comp.stopAllForeignCount()).toBe(2);
      const aviso = comp.notice();
      expect(aviso?.paragraphs.join(' ')).toContain('2 procesos ajenos');
    });

    it('el aviso dice que no es sólo lo de esta pantalla', async () => {
      // `/api/workflow/stop-all` recorre el tracker entero. El texto viejo decía
      // "de esta pantalla", que después de separar las páginas era falso y
      // peligroso: el developer confirmaba pensando que lo demás seguía vivo.
      calls.answer = { ...calls.answer, ports: [fila({ state: 'up', pid: 1 })] };
      const { comp } = await montar();

      comp.askStopAll();

      expect(comp.notice()?.paragraphs.join(' ')).toContain('todos los ambientes y clusters');
    });

    it('confirmar "Parar todo" sí incluye los ajenos', async () => {
      calls.answer = { ...calls.answer, ports: [fila({ foreign: true, state: 'up', pid: 99 })] };
      const { comp } = await montar();

      comp.askStopAll();
      await comp.confirmStopAll();
      await new Promise((r) => setTimeout(r, 0));

      expect(calls.stopAllCalls).toEqual([true]);
    });

    it('un error no hereda el rótulo de "Parar todo"', async () => {
      // El bug: el `notice-modal` recibía `confirmLabel="Parar todo"` fijo, así que
      // un aviso de "No se pudo levantar" ofrecía un botón que promete parar todo
      // lo que hay. El aviso de error es de un solo botón.
      calls.answer = { ...calls.answer, ports: [fila()] };
      calls.failStart = (err) => err;
      const { comp } = await montar();

      await comp.startPort(fila());

      expect(comp.notice()?.title).toBe('No se pudo levantar el túnel');
      expect(comp.notice()?.confirmLabel).toBeUndefined();
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
      calls.answer = { ...calls.answer, ports: [fila({ state: 'up' })] };
      const { comp } = await montar();

      expect(comp.announcement()).toBe('');
    });

    it('anuncia una fila que sube', async () => {
      calls.answer = { ...calls.answer, ports: [fila({ state: 'down' })] };
      const { comp, fixture } = await montar();

      emiteEstado(calls, { ports: [fila({ state: 'up' })] });
      await recargar(fixture);

      expect(comp.announcement()).toContain('arriba');
    });

    it('usa el puerto cuando la fila no tiene nombre', async () => {
      calls.answer = { ...calls.answer, ports: [fila({ state: 'down', label: '' })] };
      const { comp, fixture } = await montar();

      emiteEstado(calls, { ports: [fila({ state: 'up', label: '' })] });
      await recargar(fixture);

      expect(comp.announcement()).toContain(':8402');
    });

    it('no anuncia lo que pasó en otra sección', async () => {
      // El `aria-live` tiene que describir esta pantalla. Anunciar un Kafka que
      // se levantó mientras se miran los túneles es ruido para quien está
      // leyendo, y es peor que no anunciar.
      calls.answer = { ...calls.answer, resources: [recurso({ group: 'kafka', name: 'Kafdrop UI', state: 'down', pid: 1 })] };
      const { comp, fixture } = await montar();

      emiteEstado(calls, { resources: [recurso({ group: 'kafka', name: 'Kafdrop UI', state: 'up', pid: 1 })] });
      await recargar(fixture);

      expect(comp.announcement()).toBe('');
    });
  });

  // --- arrancar -------------------------------------------------------------

  describe('arrancar', () => {
    it('un start rechazado va al modal, no a la fila', async () => {
      // "Otro cluster tiene el 8403" no es un error de esta fila: ponerlo en la
      // fila mandaría al developer a la cosa equivocada. El motivo del backend
      // viaja entero y el modal lo muestra.
      calls.answer = { ...calls.answer, ports: [fila()] };
      calls.failStart = (err) => new Error(`El puerto local 8403 ya lo está usando el cluster cap2`);
      const { comp } = await montar();

      await comp.startPort(fila());
      await new Promise((r) => setTimeout(r, 0));

      expect(comp.notice()?.title).toBe('No se pudo levantar el túnel');
      expect(comp.notice()?.paragraphs.join(' ')).toContain('cap2');
    });

    it('"Levantar todos" manda el cluster de la pantalla', async () => {
      calls.answer = { ...calls.answer, ports: [fila({ port: 8402 }), fila({ port: 8403, label: 'commerce' })] };
      const { comp } = await montar();

      await comp.startAllDown();
      await new Promise((r) => setTimeout(r, 0));

      expect(calls.startedPorts).toEqual([
        { port: 8402, label: 'core-p2m-m2p', cluster: 'cap' },
        { port: 8403, label: 'commerce', cluster: 'cap' },
      ]);
    });

    it('"Levantar todos" se saltea las caídas', async () => {
      // El botón múltiple no tiene dónde decir "esto se cayó". Para eso está el
      // botón de su fila, que sí lo dice.
      calls.answer = {
        ...calls.answer,
        ports: [fila({ port: 8402, state: 'down' }), fila({ port: 8403, state: 'dead' })],
      };
      const { comp } = await montar();

      await comp.startAllDown();
      await new Promise((r) => setTimeout(r, 0));

      expect(calls.startedPorts.map((s) => s.port)).toEqual([8402]);
    });

    it('cambiar de cluster pide el estado del cluster nuevo y abandona el anterior', async () => {
      // Si la respuesta de `cap` llegara después de que el developer ya pasó a
      // `cap2`, le pisaría la tabla con los puertos del cluster equivocado. Lo que
      // lo evita es que `scope` sea parte de la identidad del stream: al cambiar, la
      // base cierra la suscripción vieja y abre otra.
      calls.answer = { ...calls.answer, ports: [fila({ port: 8402, cluster: 'cap' })] };
      const { comp } = await montar();

      comp.cluster.set('cap2');
      await new Promise((r) => setTimeout(r, 0));

      expect(calls.streams.at(-1)?.scope).toEqual({ env: 'dev', cluster: 'cap2' });
    });

    it('cambiar de ambiente también repide el estado', async () => {
      calls.answer = { ...calls.answer, ports: [fila()] };
      const { comp } = await montar();

      comp.env.set('qa');
      await new Promise((r) => setTimeout(r, 0));

      expect(calls.streams.at(-1)?.scope).toEqual({ env: 'qa', cluster: 'cap' });
    });
  });

  // --- el catálogo ----------------------------------------------------------

  describe('el catálogo', () => {
    it('rechaza un puerto fuera de rango antes de llamar al backend', async () => {
      const { comp } = await montar();

      comp.addPortValue.set('70000');
      await comp.addPort();

      expect(comp.addPortError()).toContain('entre 1 y 65535');
      expect(calls.addedPorts).toHaveLength(0);
    });

    it('pide el número si está vacío', async () => {
      const { comp } = await montar();

      comp.addPortValue.set('  ');
      await comp.addPort();

      expect(comp.addPortError()).toBe('Falta el número de puerto.');
    });

    it('el contador distingue puertos de puertos con nombre', async () => {
      calls.answer = {
        ...calls.answer,
        ports: [fila({ port: 8402, label: 'core' }), fila({ port: 8403, label: '' })],
      };
      const { comp, el } = await montar();

      expect(comp.catalogSummary()).toBe('2 puertos · 1 con nombre');
      expect(el.textContent).toContain('2 puertos · 1 con nombre');
    });

    it('una fila con conflicto lo dice', async () => {
      calls.answer = { ...calls.answer, ports: [fila({ conflict: 'el cluster cap2' })] };
      const { el } = await montar();

      const row = filaDeTabla(el);
      expect(row.textContent).toContain('ya lo está usando el cluster cap2');
      expect(row.getAttribute('data-conflict')).toBe('true');
    });

    it('sin catálogo-configurado el vacío explica qué falta', async () => {
      // El vacío se arregla distinto según si `CLUSTER_PORTS` no existe o existe
      // y está vacío: uno se resuelve editando el archivo de ambiente y el otro
      // agregando una fila.
      const { el } = await montar();

      expect(el.textContent).toContain('CLUSTER_PORTS');
    });

    it('con el catálogo configurado pero vacío dice que se agregue uno', async () => {
      calls.answer = { ...calls.answer, catalog_configured: true };
      const { el } = await montar();

      expect(el.textContent).toContain('no tiene puertos cargados');
    });

    it('quitar la fila de un túnel arriba lo para antes', async () => {
      // La fila que se borra es lo único que puede parar el túnel.
      const { comp } = await montar();

      await comp.removePort(fila({ state: 'up', pid: 5150 }));
      await new Promise((r) => setTimeout(r, 0));

      expect(calls.stopped).toEqual([{ pid: 5150, allowForeign: false }]);
      expect(calls.removed).toEqual([{ cluster: 'cap', port: 8402 }]);
    });

    it('quitar una fila apagada no intenta parar nada', async () => {
      const { comp } = await montar();

      await comp.removePort(fila());
      await new Promise((r) => setTimeout(r, 0));

      expect(calls.stopped).toHaveLength(0);
      expect(calls.removed).toEqual([{ cluster: 'cap', port: 8402 }]);
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
      expect(calls.renames).toHaveLength(0);

      await new Promise((r) => setTimeout(r, 600));
      // Una sola escritura, no tres. El catálogo es `CLUSTER_PORTS` en
      // `env.base`: escribir en cada tecla sería escribir el archivo del
      // developer treinta veces por palabra.
      expect(calls.renames).toEqual([{ cluster: 'cap', port: 8402, label: 'commerce' }]);
    });

    it('no reescribe si el nombre no cambió', async () => {
      const { comp } = await montar();

      comp.writeLabel(fila({ label: 'core' }), 'core');
      await new Promise((r) => setTimeout(r, 600));

      expect(calls.renames).toHaveLength(0);
    });

    it('cada fila tiene su propio debounce', async () => {
      const { comp } = await montar();

      comp.writeLabel(fila({ port: 8402 }), 'core');
      comp.writeLabel(fila({ port: 8403 }), 'commerce');
      await new Promise((r) => setTimeout(r, 600));

      expect(calls.renames.map((r) => r.port)).toEqual([8402, 8403]);
    });
  });

  // --- el resumen -----------------------------------------------------------

  describe('la barra de resumen', () => {
    it('cuenta arriba, levantando y con problemas', async () => {
      calls.answer = {
        ...calls.answer,
        ports: [
          fila({ port: 8402, state: 'up', pid: 1 }),
          fila({ port: 8403, state: 'starting', pid: 2 }),
          fila({ port: 8404, state: 'error', pid: 3 }),
        ],
      };
      const { comp } = await montar();

      expect(comp.summary()).toBe('1 arriba · 1 levantando · 1 con problemas');
    });

    it('acusa los levantados fuera de la web', async () => {
      calls.answer = { ...calls.answer, ports: [fila({ state: 'up', pid: 1, foreign: true })] };
      const { comp } = await montar();

      expect(comp.summary()).toContain('1 levantado fuera de la web');
    });

    it('cuenta sólo las filas de esta página', async () => {
      // El resumen es de la pantalla: un Kafka arriba mientras se miran los
      // túneles no aparece en el conteo. Para el alcance de "Parar todo" está el
      // aviso, que sí mira el tracker entero.
      calls.answer = {
        ...calls.answer,
        ports: [fila({ state: 'up', pid: 1 })],
        resources: [recurso({ group: 'kafka', name: 'Kafdrop UI', state: 'up', pid: 2 })],
      };
      const { comp } = await montar();

      expect(comp.summary()).toBe('1 arriba');
    });

    it('"Parar todo" se deshabilita cuando no hay nada vivo', async () => {
      const { comp, el } = await montar();

      expect(comp.anythingAlive()).toBe(false);
      const stopAll = [...el.querySelectorAll('button')].find((b) => b.textContent?.includes('Parar todo'));
      expect(stopAll?.disabled).toBe(true);
    });
  });

  // --- sin puerto conocido --------------------------------------------------

  describe('los túneles sin puerto conocido', () => {
    it('no se muestran si no hay ninguno', async () => {
      const { el } = await montar();

      expect(el.textContent).not.toContain('Sin puerto conocido');
    });

    it('se muestran aparte cuando el tracker los conoce y ninguna fila puede ubicarlos', async () => {
      calls.answer = {
        ...calls.answer,
        unattributed: [{ pid: 404, resource: 'session-manager-plugin', env: 'dev', log_file: 'x.log' }],
      };
      const { el } = await montar();

      expect(el.textContent).toContain('Sin puerto conocido');
      expect(el.textContent).toContain('session-manager-plugin');
      expect(el.textContent).toContain('pid 404');
    });
  });

  // --- errores --------------------------------------------------------------

  describe('cuando el backend se corta', () => {
    it('la caja aparece con un corte y dice que no se pudo conectar', async () => {
      const { comp, fixture } = await montar();

      // El `EventSource` no puede decir por qué se cortó: sólo avisa que se cortó.
      emiteCaida(calls);
      fixture.detectChanges();

      expect(comp.readError()).toContain('No se pudo conectar');
      expect(fixture.nativeElement.querySelector('.error-box')).not.toBeNull();
    });

    it('la caja no aparece mientras llegan estados', async () => {
      const { comp, fixture } = await montar();

      emiteEstado(calls);
      fixture.detectChanges();

      expect(comp.readError()).toBe('');
      expect(fixture.nativeElement.querySelector('.error-box')).toBeNull();
    });

    it('la caja se va sola cuando el servidor vuelve', async () => {
      // El bug: el error quedaba pegado para siempre. Una caja roja que sobrevive a
      // la recuperación termina diciendo que no se puede leer algo que hace un rato
      // se está leyendo — y el `EventSource` reconecta solo, así que el frame que la
      // borra es el mismo que repinta la tabla.
      const { comp, fixture } = await montar();

      emiteCaida(calls);
      fixture.detectChanges();
      expect(comp.readError()).not.toBe('');

      emiteEstado(calls, { ports: [fila({ state: 'up' })] });
      fixture.detectChanges();

      expect(comp.readError()).toBe('');
      expect(fixture.nativeElement.querySelector('.error-box')).toBeNull();
    });

    it('el estado anterior sobrevive al corte', async () => {
      // No se vacía la tabla mientras no hay respuesta: la última foto buena es
      // mejor que una pantalla en blanco que parece "no hay nada corriendo".
      calls.answer = { ...calls.answer, ports: [fila({ state: 'up' })] };
      const { comp } = await montar();

      emiteCaida(calls);

      expect(comp.ports()).toHaveLength(1);
    });
  });

  // --- el ambiente ------------------------------------------------------------

  describe('el ambiente que ofrece', () => {
    it('no ofrece los ambientes locales', async () => {
      // `local` es un ambiente de *base de datos*: se alcanza por TCP contra el
      // MySQL de la máquina, y no tiene SSM ni Parameter Store. Los túneles del
      // catálogo son sesiones SSM contra el cluster, así que ofrecerlo acá es
      // ofrecer una opción que la pantalla misma dice que no sirve.
      //
      // Es el mismo filtro que usan las páginas de parámetros, y por el mismo
      // motivo: `local` es un ambiente de base de datos, no de AWS.
      calls.envs = [
        { env: 'local', is_local: true },
        { env: 'qa' },
        { env: 'uat' },
      ];
      const { comp } = await montar();

      expect(comp.envList()).toEqual([{ env: 'qa' }, { env: 'uat' }]);
      // Y el ambiente con el que se abre el stream es uno de los que existen.
      expect(comp.env()).toBe('qa');
      expect(calls.streams.at(-1)?.scope?.env).toBe('qa');
    });
  });

  // --- el stream ------------------------------------------------------------

  describe('la conexión', () => {
    it('se abre una vez y trae el estado entero sin pedir nada', async () => {
      calls.answer = { ...calls.answer, ports: [fila({ state: 'up' })] };
      const { comp } = await montar();

      expect(calls.streams).toHaveLength(1);
      expect(comp.ports()).toHaveLength(1);
    });

    it('se abre con un ambiente que existe, no con el que trae por defecto', async () => {
      // 'dev' es el ambiente que la página trae por defecto, pero es un ambiente
      // *real*: si la máquina no lo tiene, el backend contesta 400 y la pantalla
      // lo lee como "no se pudo conectar con el servidor". Con la lista de
      // ambientes, el que se pide es uno que existe.
      calls.envs = [{ env: 'local' }, { env: 'qa' }];
      const { comp } = await montar();

      expect(comp.env()).toBe('local');
      expect(calls.streams.at(-1)?.scope).toEqual({ env: 'local', cluster: 'cap' });
    });

    it('no abre el stream antes de saber qué ambientes hay', async () => {
      // El stream se abre cuando la lista llegó, no antes: antes es una
      // pregunta por un ambiente que puede no existir.
      calls.envs = [{ env: 'qa' }];
      const { comp } = await montar();

      expect(calls.envCalls).toBe(1);
      expect(calls.streams.at(-1)?.scope?.env).toBe('qa');
      expect(comp.env()).toBe('qa');
    });

    it('se cierra al abandonar la página', async () => {
      const { fixture } = await montar();

      fixture.destroy();

      expect(calls.streams[0].closed).toBe(true);
    });

    it('cambiar de cluster cierra el anterior y abre otro', async () => {
      // Si el stream viejo siguiera abierto, su frame podría llegar después de que
      // el developer ya pasó a `cap2` y pisarle la tabla con los puertos del cluster
      // equivocado. Cerrarlo es lo que lo evita.
      const { comp } = await montar();
      const antes = calls.streams[0];

      comp.cluster.set('cap2');
      await new Promise((r) => setTimeout(r, 0));

      expect(antes.closed).toBe(true);
      expect(calls.streams.at(-1)?.scope).toEqual({ env: 'dev', cluster: 'cap2' });
    });

    it('cambiar de ambiente también reabre la conexión', async () => {
      const { comp } = await montar();

      comp.env.set('qa');
      await new Promise((r) => setTimeout(r, 0));

      expect(calls.streams.at(-1)?.scope).toEqual({ env: 'qa', cluster: 'cap' });
    });
  });
});
