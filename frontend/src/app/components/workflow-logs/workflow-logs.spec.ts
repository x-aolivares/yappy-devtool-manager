/**
 * El panel de logs, que las cuatro páginas de Workflow comparten.
 *
 * Los tests son los de la pantalla única, mudados acá. No cambiaron: el panel es
 * el mismo código con otro dueño, y lo que estaba probado es justamente lo que no
 * puede volver a rompirse sin que se note —el 404 que corta el poll, el corte de
 * red que no lo corta, el texto que se conserva cuando el proceso se fue.
 *
 * Lo que cambió es quién decide *qué* se mira: antes la página knew; ahora el
 * panel recibe `rows` y ofrece el primer recurso con archivo de log. Eso es lo
 * que se prueba en "el atajo de la barra".
 */
import { TestBed } from '@angular/core/testing';

import { WorkflowLogs } from './workflow-logs';
import {
  WorkflowCalls, callsVacios, corteDeRed, kafkaArriba, pidSinTrackear,
  workflowProviders,
} from '../../core/workflow-doubles';
import type { WorkflowRow } from '../../core/workflow-section-page';

describe('WorkflowLogs', () => {
  let calls: WorkflowCalls;

  beforeEach(async () => {
    calls = callsVacios();
    await TestBed.configureTestingModule({
      imports: [WorkflowLogs],
      providers: workflowProviders(calls),
    }).compileComponents();
  });

  function montar(rows: readonly WorkflowRow[] = []) {
    const fixture = TestBed.createComponent(WorkflowLogs);
    fixture.componentRef.setInput('rows', rows);
    fixture.detectChanges();
    return { comp: fixture.componentInstance, el: fixture.nativeElement as HTMLElement, fixture };
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));

  // --- el ciclo de vida del panel ------------------------------------------

  describe('no se lee el log hasta que alguien lo pide', () => {
    it('con una fila arriba y con archivo tampoco sale ninguna request', () => {
      // La regla de dedo del developer: el panel sigue un pid porque él apretó
      // "Ver logs", no porque la pantalla esté mirando.
      const { comp } = montar([kafkaArriba()]);

      expect(calls.logCalls).toHaveLength(0);
      expect(comp.scope()).toBeNull();
    });

    it('abrirlo pide el log del pid de la fila, una sola vez', async () => {
      const { comp } = montar();
      calls.logText = 'INFO Kafka Server started\n';

      comp.open(kafkaArriba());
      await tick();

      // Y **una sola vez**: el poll se programa con `setTimeout`, no en bucle. La
      // regresión que se vigila acá es que `appendLog` leyera el offset dentro del
      // `effect` — el effect dependía de él, cada vuelta lo escribía y volvía a
      // dispararse: cien requests en el tiempo de una.
      expect(calls.logCalls).toEqual([{ pid: 21224, offset: 0 }]);
      expect(comp.text()).toContain('Kafka Server started');
    });

    it('cada lectura pide desde el offset que volvió la anterior', async () => {
      const { comp } = montar();
      calls.logText = 'uno\n';

      comp.open(kafkaArriba());
      await tick();
      calls.logText = 'dos\n';
      comp.pollForTest();
      await tick();

      // El offset que vuelve es el tamaño acumulado, no un cursor del backend: la
      // segunda lectura pide desde el final exacto de la primera y así no se
      // solapan las líneas del medio.
      expect(calls.logCalls.map((c) => c.offset)).toEqual([0, 4]);
      expect(comp.text()).toBe('uno\ndos\n');
    });

    it('cerrar y volver a abrir vuelve a preguntar desde el principio', async () => {
      const { comp } = montar();
      calls.logError = pidSinTrackear();

      comp.open(kafkaArriba());
      await tick();
      comp.close();

      calls.logError = null;
      calls.logText = 'hola\n';
      comp.open(kafkaArriba());
      await tick();
      comp.pollForTest();
      await tick();

      expect(comp.gone()).toBe(false);
      // El offset se reinicia al reabrir: si no, la segunda lectura pediría desde
      // el final del archivo anterior y el log nuevo aparecería a medias.
      expect(calls.logCalls.map((c) => c.offset)).toEqual([0, 0, 5]);
    });
  });

  // --- los errores ----------------------------------------------------------

  describe('un 404 corta el poll para siempre', () => {
    it('y deja de preguntar', async () => {
      // El bug: el `catch` se tragaba el 404 como si fuera un corte de red. Un
      // pid que dejó de estar trackeado no vuelve, así que el panel seguía
      // golpeando /logs cada 1,5s para siempre — veinte 404s seguidos en el log
      // del server, uno detrás de otro.
      calls.logError = pidSinTrackear();
      const { comp } = montar();

      comp.open(kafkaArriba());
      await tick();
      expect(comp.gone()).toBe(true);
      expect(calls.logCalls).toHaveLength(1);

      // Los ticks que sólo pueden volver a fallar.
      comp.pollForTest();
      comp.pollForTest();
      await tick();

      expect(calls.logCalls).toHaveLength(1);
    });

    it('un corte de red no cierra el panel ni corta el poll', async () => {
      // Al revés del 404: acá puede contestar el tick siguiente, así que tragarse
      // el error en silencio es lo correcto. Cerrar el panel por un corte de red
      // sería perder el log que el developer estaba leyendo por un cable que se
      // movió.
      calls.logError = corteDeRed();
      const { comp, el } = montar();

      comp.open(kafkaArriba());
      await tick();

      expect(comp.gone()).toBe(false);
      expect(comp.scope()).toBe(21224);
      expect(el.querySelector('.log-panel__note')).toBeNull();

      calls.logError = null;
      comp.pollForTest();
      await tick();

      expect(calls.logCalls).toHaveLength(2);
    });

    it('un 404 de un panel viejo no mata el log del que se está mostrando', async () => {
      // La request del panel anterior puede volver después de que el developer
      // abrió el de otro recurso. Si el 404 llegara sin preguntar de quién es,
      // dejaría al panel nuevo sin poll para siempre.
      calls.logError = pidSinTrackear();
      const { comp } = montar();

      comp.open(kafkaArriba({ pid: 21224 }));
      await tick();
      expect(comp.gone()).toBe(true);

      calls.logError = null;
      comp.open(kafkaArriba({ pid: 33940 }));
      await tick();

      expect(comp.gone()).toBe(false);
      comp.pollForTest();
      await tick();

      expect(calls.logCalls.filter((c) => c.pid === 33940).length).toBeGreaterThan(0);
    });
  });

  // --- lo que se ve ---------------------------------------------------------

  describe('lo que se ve en el panel', () => {
    it('un proceso que murió sin escribir una línea no dice "Buscando…" para siempre', async () => {
      calls.logError = pidSinTrackear();
      const { comp, el } = montar();

      comp.open(kafkaArriba());
      await tick();

      expect(el.textContent).toContain('Sin contenido todavía.');
    });

    it('queda con lo que se leyó y avisa que el proceso se fue', async () => {
      // Un panel congelado en silencio es indistinguible de uno que anda bien.
      // El texto que ya se leyó es justo lo que el developer estaba mirando
      // cuando paró Kafka, así que se conserva y se explica el corte al lado.
      calls.logText = 'INFO Kafka Server started (registered)\n';
      const { comp, el, fixture } = montar();

      comp.open(kafkaArriba());
      await tick();
      fixture.detectChanges();
      expect(el.querySelector('.log-view')?.textContent).toContain('Kafka Server started');

      calls.logError = pidSinTrackear();
      comp.pollForTest();
      await tick();
      fixture.detectChanges();

      expect(el.querySelector('.log-view')?.textContent).toContain('Kafka Server started');
      expect(el.querySelector('.log-panel__note')?.textContent).toContain('ya no está en el tracker');
      // El panel sigue abierto: lo que se leyó no se tira a la basura.
      expect(comp.scope()).toBe(21224);
    });

    it('el texto se corta por arriba, no por abajo', async () => {
      const { comp } = montar();
      calls.logText = 'x'.repeat(25000);

      comp.open(kafkaArriba());
      await tick();

      expect(comp.text()).toHaveLength(20000);
    });

    it('el panel está oculto mientras no hay pid', () => {
      const { el } = montar();

      expect(el.querySelector('.log-panel')?.hasAttribute('hidden')).toBe(true);
    });
  });

  // --- el atajo de la barra -------------------------------------------------

  describe('el atajo de la barra', () => {
    it('ofrece el pid que se está mirando', async () => {
      // La mitad que no se toca: el recurso que el developer acaba de pedir es el
      // que la barra ofrece, aunque haya otro con log más arriba en la grilla.
      const { comp } = montar([kafkaArriba()]);

      comp.open(kafkaArriba());
      await tick();

      expect(comp.gone()).toBe(false);
      expect(comp.firstLogPid()).toBe(21224);
    });

    it('no ofrece nada si ninguna fila tiene archivo de log', () => {
      // Una fila levantándose sin `log_file` no es un bug: el proceso todavía no
      // abrió el archivo. El botón queda deshabilitado en vez de abrir un panel
      // que va a decir "Sin contenido todavía" para siempre.
      const { comp } = montar([kafkaArriba({ log_file: '', state: 'starting' })]);

      expect(comp.firstLogPid()).toBeNull();
      expect(comp.hasFirstLog()).toBe(false);
    });

    it('ignora las filas que no pueden tener log todavía', () => {
      // `canShowLogs` es `up` o `starting`: una fila caída puede tener archivo
      // viejo, y abrirlo sería mostrar un log que no explica el estado actual.
      const { comp } = montar([kafkaArriba({ state: 'down' })]);

      expect(comp.hasFirstLog()).toBe(false);
    });

    it('deja de ofrecer un pid que ya no está en el tracker', async () => {
      // El bug: el `pid` sobrevive al `gone`, así que el botón quedaba
      // habilitado apuntando a un pid que el backend ya no trackea. Cada click
      // costaba un 404 y volvía a mostrar el aviso — el mismo callejón sin
      // salida, pedido por el developer.
      calls.logError = pidSinTrackear();
      const { comp } = montar([kafkaArriba()]);

      comp.open(kafkaArriba());
      await tick();

      // La fila sigue dibujándose arriba y con su `log_file`: es el snapshot del
      // último poll. Por eso el pid muerto se filtra también de la búsqueda por
      // filas — si no, el fallback devolvería el mismo pid y el botón quedaría
      // habilitado igual.
      expect(comp.gone()).toBe(true);
      expect(comp.firstLogPid()).toBeNull();
      expect(comp.hasFirstLog()).toBe(false);
    });

    it('pasa al siguiente recurso con log cuando el que se mira se fue', async () => {
      const rows = [
        kafkaArriba(),
        kafkaArriba({ key: 'kafka-server', pid: 33940, log_file: 'kafka-server-33940.log' }),
      ];
      calls.logError = pidSinTrackear();
      const { comp } = montar(rows);

      comp.open(kafkaArriba());
      await tick();

      expect(comp.firstLogPid()).toBe(33940);
      expect(comp.hasFirstLog()).toBe(true);

      // Y el click lleva al panel al otro recurso, sin volver al pid muerto.
      calls.logError = null;
      comp.openFirst();
      await tick();

      expect(comp.scope()).toBe(33940);
      expect(comp.gone()).toBe(false);
      expect(calls.logCalls.filter((c) => c.pid === 21224).length).toBe(1);
    });

    it('sin pid no hace nada', () => {
      // El botón llega deshabilitado desde la barra, pero el método no puede dar
      // por eso: `openFirst` con un pid inventado dejaría el panel pidiendo un log
      // que nadie pidió.
      const { comp } = montar([]);

      comp.openFirst();

      expect(comp.scope()).toBeNull();
      expect(calls.logCalls).toHaveLength(0);
    });
  });
});
