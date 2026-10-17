/**
 * El panel de logs, que las cuatro páginas de Workflow comparten.
 *
 * Los tests son los de la pantalla única, mudados acá y después mudados otra vez
 * cuando el panel pasó de consultar a escuchar. No cambiaron las reglas: el panel
 * sigue un pid porque alguien apretó "Ver logs", un `gone` lo corta para siempre,
 * un corte de red no, y el texto que ya se leyó no se tira a la basura.
 *
 * Lo que cambió es el mecanismo, y por eso los dobles empujaron frames en vez de
 * devolver promesas: con el stream no hay tick que disparar, así que "el log creció"
 * es algo que el test produce con `emiteLog` y no algo que espera.
 */
import { TestBed } from '@angular/core/testing';

import { WorkflowLogs } from './workflow-logs';
import {
  WorkflowCalls, callsVacios, emiteLog, emiteLogGone, kafkaArriba, workflowProviders,
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

  /** Los pids de las conexiones de log abiertas. */
  const abiertos = () => calls.logStreams.map((s) => s.pid);

  // --- el ciclo de vida del panel ------------------------------------------

  describe('no se lee el log hasta que alguien lo pide', () => {
    it('con una fila arriba y con archivo tampoco se abre ninguna conexión', () => {
      // La regla de dedo del developer: el panel sigue un pid porque él apretó
      // "Ver logs", no porque la pantalla esté mirando.
      const { comp } = montar([kafkaArriba()]);

      expect(abiertos()).toEqual([]);
      expect(comp.scope()).toBeNull();
    });

    it('abrirlo abre una conexión al pid de la fila, una sola vez', async () => {
      const { comp } = montar();
      calls.logText = 'INFO Kafka Server started\n';

      comp.open(kafkaArriba());
      await tick();

      // Y **una sola conexión**: la regresión que se vigila acá es que el handler
      // escribiera `text` u `offset` sin `untracked`, el efecto dependiera de eso,
      // y cada frame disparara el efecto otra vez — el panel comiéndose sus propios
      // streams en bucle, igual que se comía sus propios polls.
      expect(abiertos()).toEqual([21224]);
      expect(comp.text()).toContain('Kafka Server started');
    });

    it('cada frame se suma al texto, sin pedir nada', async () => {
      const { comp } = montar();
      calls.logText = 'uno\n';

      comp.open(kafkaArriba());
      await tick();
      emiteLog(calls, 'dos\n');
      await tick();

      // El cursor vive en la conexión: el panel no tiene offset que mandar ni que
      // acertar. Sólo llega lo que el archivo agrego.
      expect(comp.text()).toBe('uno\ndos\n');
    });

    it('cerrar y volver a abrir abre una conexión nueva desde el principio', async () => {
      const { comp } = montar();

      comp.open(kafkaArriba());
      await tick();
      comp.close();
      await tick();

      calls.logText = 'hola\n';
      comp.open(kafkaArriba());
      await tick();

      expect(comp.gone()).toBe(false);
      expect(abiertos()).toEqual([21224, 21224]);
      expect(comp.text()).toBe('hola\n');
    });

    it('la conexión se cierra al soltar el panel', async () => {
      const { comp, fixture } = montar();

      comp.open(kafkaArriba());
      await tick();
      expect(calls.logStreams[0].closed).toBe(false);

      fixture.destroy();

      // Sin esto, abrir y cerrar "Ver logs" veinte veces dejaría veinte conexiones
      // abiertas mirando un archivo.
      expect(calls.logStreams[0].closed).toBe(true);
    });
  });

  // --- los errores ----------------------------------------------------------

  describe('un gone corta el panel para siempre', () => {
    it('y deja de abrir conexiones', async () => {
      // El bug: el `catch` se tragaba el 404 como si fuera un corte de red. Un pid
      // que dejó de estar trackeado no vuelve, así que el panel seguía golpeando
      // /logs cada 1,5s para siempre — veinte 404s seguidos en el log del server.
      // Ahora el server avisa con un frame y cierra: no hay ni una request de sobra.
      const { comp } = montar();

      comp.open(kafkaArriba());
      await tick();
      emiteLogGone(calls);
      await tick();

      expect(comp.gone()).toBe(true);
      expect(abiertos()).toEqual([21224]);
    });

    it('un corte de red no cierra el panel', async () => {
      // Al revés del gone: una conexión que se corta puede volver, así que tragarse
      // el corte en silencio es lo correcto. Cerrar el panel por eso sería perder el
      // log que el developer estaba leyendo por un cable que se movió.
      //
      // El `EventSource` reporta un corte como un frame vacío, y un frame vacío no
      // cambia nada: el texto sigue, el panel sigue abierto.
      const { comp, el } = montar();

      comp.open(kafkaArriba());
      await tick();
      emiteLog(calls, '');
      await tick();

      expect(comp.gone()).toBe(false);
      expect(comp.scope()).toBe(21224);
      expect(el.querySelector('.log-panel__note')).toBeNull();
    });

    it('un gone de un panel viejo no mata el log del que se está mostrando', async () => {
      // Un frame puede llegar después de que el developer abrió el de otro recurso.
      // Si el `gone` se aplicara sin preguntar de quién es, dejaría al panel nuevo
      // muerto sin que nadie se lo haya dicho.
      const { comp } = montar();

      comp.open(kafkaArriba({ pid: 21224 }));
      await tick();
      emiteLogGone(calls, 21224);
      await tick();
      expect(comp.gone()).toBe(true);

      comp.open(kafkaArriba({ pid: 33940 }));
      await tick();

      expect(comp.gone()).toBe(false);
      expect(abiertos()).toEqual([21224, 33940]);
    });
  });

  // --- lo que se ve ---------------------------------------------------------

  describe('lo que se ve en el panel', () => {
    it('un proceso que murió sin escribir una línea no dice "Buscando…" para siempre', async () => {
      const { comp, el } = montar();

      comp.open(kafkaArriba());
      await tick();
      emiteLogGone(calls);
      await tick();

      expect(comp.loaded()).toBe(true);
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

      emiteLogGone(calls);
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
      // habilitado apuntando a un pid que el backend ya no trackea.
      const { comp } = montar([kafkaArriba()]);

      comp.open(kafkaArriba());
      await tick();
      emiteLogGone(calls);
      await tick();

      // La fila sigue dibujándose arriba y con su `log_file`: es el último estado
      // que llegó por el stream. Por eso el pid muerto se filtra también de la
      // búsqueda por filas — si no, el fallback devolvería el mismo pid y el botón
      // quedaría habilitado igual.
      expect(comp.gone()).toBe(true);
      expect(comp.firstLogPid()).toBeNull();
      expect(comp.hasFirstLog()).toBe(false);
    });

    it('pasa al siguiente recurso con log cuando el que se mira se fue', async () => {
      const rows = [
        kafkaArriba(),
        kafkaArriba({ key: 'kafka-server', pid: 33940, log_file: 'kafka-server-33940.log' }),
      ];
      const { comp } = montar(rows);

      comp.open(kafkaArriba());
      await tick();
      emiteLogGone(calls);
      await tick();

      expect(comp.firstLogPid()).toBe(33940);
      expect(comp.hasFirstLog()).toBe(true);

      // Y el click lleva al panel al otro recurso, sin volver al pid muerto.
      comp.openFirst();
      await tick();

      expect(comp.scope()).toBe(33940);
      expect(comp.gone()).toBe(false);
      expect(abiertos().filter((pid) => pid === 21224)).toHaveLength(1);
    });

    it('sin pid no hace nada', () => {
      // El botón llega deshabilitado desde la barra, pero el método no puede dar
      // por eso: `openFirst` con un pid inventado dejaría el panel siguiendo un log
      // que nadie pidió.
      const { comp } = montar([]);

      comp.openFirst();

      expect(comp.scope()).toBeNull();
      expect(abiertos()).toEqual([]);
    });
  });
});