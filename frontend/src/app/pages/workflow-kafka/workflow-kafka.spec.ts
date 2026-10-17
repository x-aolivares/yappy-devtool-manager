/**
 * Kafka local.
 *
 * Es la página de la que las otras dos se parecen, así que lo que se verifica acá
 * es lo propio de *toda* página de tarjetas menos el filtro de grupo:
 *
 * - **La página dibuja sólo su grupo.** Kafka y la base de datos salen del mismo
 *   endpoint y comparten la grilla; si el filtro se rompe, la página muestra los
 *   recursos de las otras y el developer para un Kafka que no es suyo.
 * - **La página no pide ámbito.** Es full local, así que el poll sale sin `env`
 *   ni `cluster` y no pide la lista de ambientes. Es la diferencia de Kafka con las
 *   otras tres secciones, y la razón de que exista `scope`.
 * - **El resumen cuenta lo de esta pantalla**, no el tracker entero.
 * - **El "Ver logs" de una fila abre el panel compartido** con el pid de esa fila.
 */
import { TestBed } from '@angular/core/testing';

import { WorkflowKafkaPage } from './workflow-kafka';
import {
  WorkflowCalls, callsVacios, emiteCaida, emiteEstado, kafkaArriba, kafkaBloqueada, recurso,
  workflowProviders,
} from '../../core/workflow-doubles';

describe('WorkflowKafkaPage', () => {
  let calls: WorkflowCalls;

  beforeEach(async () => {
    calls = callsVacios();
    await TestBed.configureTestingModule({
      imports: [WorkflowKafkaPage],
      providers: workflowProviders(calls),
    }).compileComponents();
  });

  async function montar() {
    const fixture = TestBed.createComponent(WorkflowKafkaPage);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return {
      comp: fixture.componentInstance,
      el: fixture.nativeElement as HTMLElement,
      fixture,
    };
  }

  const nombres = (el: HTMLElement): string[] =>
    [...el.querySelectorAll('.res-card__name')].map((n) => n.textContent?.trim() ?? '');

  function tarjeta(el: HTMLElement, nombre: string): HTMLElement {
    const card = [...el.querySelectorAll<HTMLElement>('.res-card')].find((c) =>
      c.querySelector('.res-card__name')?.textContent?.includes(nombre),
    );
    if (!card) throw new Error(`No está la tarjeta de ${nombre}`);
    return card;
  }

  const botonesDe = (el: HTMLElement, nombre: string): string[] =>
    [...tarjeta(el, nombre).querySelectorAll('button')].map((b) => b.textContent?.trim() ?? '');

  // --- el filtro de grupo ---------------------------------------------------

  describe('qué filas dibuja', () => {
    it('sólo las del grupo kafka', async () => {
      calls.answer = {
        ...calls.answer,
        resources: [
          kafkaArriba(),
          recurso({ key: 'bastion', group: 'named', name: 'Bastion' }),
          recurso({ key: 'db', group: 'db', name: 'Tunnel Aurora' }),
        ],
      };
      const { el } = await montar();

      expect(nombres(el)).toEqual(['Kafdrop UI']);
    });

    it('no hay tarjetas sin recursos que lo digan', async () => {
      const { el } = await montar();

      expect(nombres(el)).toEqual([]);
      expect(el.querySelector('.res-grid__empty')?.textContent).toContain('No hay Kafka corriendo');
    });
  });

  // --- el resumen -----------------------------------------------------------

  describe('la barra de resumen', () => {
    it('cuenta sólo Kafka, no el resto del tracker', async () => {
      calls.answer = {
        ...calls.answer,
        resources: [
          kafkaArriba(),
          recurso({ key: 'db', group: 'db', name: 'Tunnel Aurora', state: 'up', pid: 2 }),
          recurso({ key: 'bastion', group: 'named', name: 'Bastion', state: 'starting', pid: 3 }),
        ],
      };
      const { comp } = await montar();

      expect(comp.summary()).toBe('1 arriba');
    });

    it('"Parar todo" se habilita con un Kafka arriba', async () => {
      calls.answer = { ...calls.answer, resources: [kafkaArriba()] };
      const { comp, el } = await montar();

      expect(comp.anythingAlive()).toBe(true);
      const stopAll = [...el.querySelectorAll('button')].find((b) => b.textContent?.includes('Parar todo'));
      expect(stopAll?.disabled).toBe(false);
    });

    it('el "Ver logs" de la barra se habilita si algún Kafka tiene archivo', async () => {
      calls.answer = { ...calls.answer, resources: [kafkaArriba()] };
      const { el } = await montar();

      const barra = el.querySelector('.summary-bar');
      const verLogs = [...(barra?.querySelectorAll<HTMLButtonElement>('button') ?? [])].find((b) =>
        b.textContent?.includes('Ver logs'),
      );
      expect(verLogs?.disabled).toBe(false);
    });
  });

  // --- el ámbito ------------------------------------------------------------

  describe('el ámbito que pide', () => {
    it('abre el stream sin ambiente ni cluster', async () => {
      await montar();

      expect(calls.streams).toHaveLength(1);
      expect(calls.streams[0].scope).toEqual({});
    });

    it('no pide la lista de ambientes: acá no hay selector', async () => {
      const { comp } = await montar();

      expect(calls.envCalls).toBe(0);
      expect(comp.envList()).toEqual([]);
    });

    it('"Levantar" va sin ambiente: Kafka no se levanta contra uno', async () => {
      calls.answer = { ...calls.answer, resources: [kafkaArriba({ state: 'down', pid: null })] };
      const { el, fixture } = await montar();

      const levantar = [...tarjeta(el, 'Kafdrop UI').querySelectorAll('button')].find(
        (b) => b.textContent?.trim() === 'Levantar',
      );
      levantar?.click();
      await new Promise((r) => setTimeout(r, 0));
      fixture.detectChanges();

      expect(calls.startEnvs).toEqual([undefined]);
    });
  });

  // --- el stream ------------------------------------------------------------

  describe('la conexión', () => {
    it('la fila se mueve cuando llega un frame, sin pedir nada', async () => {
      // El comportamiento que reemplaza al poll: el backend publica que el
      // supervisor escribió el veredicto, y la tarjeta cambia. Nadie interroga.
      calls.answer = { ...calls.answer, resources: [kafkaArriba({ state: 'starting', pid: 9001 })] };
      const { el, fixture } = await montar();
      expect(tarjeta(el, 'Kafdrop UI').textContent).toContain('levantando');

      emiteEstado(calls, { resources: [kafkaArriba()] });
      fixture.detectChanges();

      expect(tarjeta(el, 'Kafdrop UI').textContent).not.toContain('levantando');
    });

    it('anuncia el cambio de estado que trae el frame', async () => {
      calls.answer = { ...calls.answer, resources: [kafkaArriba({ state: 'down', pid: null })] };
      const { comp, fixture } = await montar();

      emiteEstado(calls, { resources: [kafkaArriba()] });
      fixture.detectChanges();

      expect(comp.announcement()).toContain('Kafdrop UI');
    });

    it('la caja de error sale y se va sola', async () => {
      const { comp, fixture } = await montar();

      emiteCaida(calls);
      fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('.error-box')).not.toBeNull();

      emiteEstado(calls);
      fixture.detectChanges();

      expect(comp.readError()).toBe('');
      expect(fixture.nativeElement.querySelector('.error-box')).toBeNull();
    });

    it('el pedido del log dice de qué sección salió', async () => {
      // El access log del server imprime el path y nada más. Con las cuatro
      // secciones sobre el mismo endpoint, sin esto el log del server tiene
      // treinta líneas idénticas y no se sabe cuál de ellas estaba preguntando.
      calls.answer = { ...calls.answer, resources: [kafkaArriba()] };
      const { el } = await montar();

      [...tarjeta(el, 'Kafdrop UI').querySelectorAll('button')]
        .find((b) => b.textContent?.trim() === 'Ver logs')
        ?.click();
      await new Promise((r) => setTimeout(r, 0));

      expect(calls.logStreams.map((s) => [s.pid, s.section])).toEqual([[21224, 'kafka']]);
    });

    it('se cierra al abandonar la página', async () => {
      const { fixture } = await montar();

      fixture.destroy();

      expect(calls.streams[0].closed).toBe(true);
    });
  });

  // --- las acciones ---------------------------------------------------------

  describe('qué hace cada botón', () => {
    it('"Levantar" manda la key del recurso', async () => {
      calls.answer = { ...calls.answer, resources: [kafkaArriba({ state: 'down', pid: null })] };
      const { el, fixture } = await montar();

      const levantar = [...tarjeta(el, 'Kafdrop UI').querySelectorAll('button')].find(
        (b) => b.textContent?.trim() === 'Levantar',
      );
      levantar?.click();
      await new Promise((r) => setTimeout(r, 0));
      fixture.detectChanges();

      expect(calls.startedKeys).toEqual(['kafka-ui']);
    });

    it('"Ver logs" de la fila abre el panel con el pid de esa fila', async () => {
      calls.answer = { ...calls.answer, resources: [kafkaArriba()] };
      const { comp, el } = await montar();

      const verLogs = [...tarjeta(el, 'Kafdrop UI').querySelectorAll('button')].find(
        (b) => b.textContent?.trim() === 'Ver logs',
      );
      verLogs?.click();
      await new Promise((r) => setTimeout(r, 0));

      expect(comp.logs()?.scope()).toBe(21224);
      expect(calls.logStreams.map((s) => [s.pid, s.section])).toEqual([[21224, 'kafka']]);
    });

    it('una fila ajena no se puede parar desde la tarjeta', async () => {
      calls.answer = { ...calls.answer, resources: [kafkaArriba({ foreign: true })] };
      const { el } = await montar();

      const card = tarjeta(el, 'Kafdrop UI');
      expect(card.classList).toContain('res-card--foreign');
      expect([...card.querySelectorAll('button')].map((b) => b.textContent?.trim())).not.toContain('Parar');
    });
  });

  // --- el arreglo que propone el backend ----------------------------------

  describe('el storage bloqueado', () => {
    it('el botón aparece sólo cuando el backend lo dice', async () => {
      // Kafdrop sin `fix` y el broker con `fix`: el mismo dibujo, dos filas, y sólo
      // una tiene el botón. Es la diferencia entre "el backend lo sugirió" y
      // "apareció porque la fila es de Kafka".
      calls.answer = { ...calls.answer, resources: [kafkaArriba(), kafkaBloqueada()] };
      const { el } = await montar();

      expect(botonesDe(el, 'Kafdrop UI')).not.toContain('Limpiar el broker');
      expect(botonesDe(el, 'Kafka Server')).toContain('Limpiar el broker');
    });

    it('pide confirmación y dice qué se pierde antes de hacerlo', async () => {
      calls.answer = { ...calls.answer, resources: [kafkaBloqueada()] };
      const { comp, fixture } = await montar();

      comp.askFix(kafkaBloqueada());
      fixture.detectChanges();

      const aviso = comp.notice();
      expect(aviso?.tone).toBe('danger');
      expect(aviso?.confirmLabel).toBe('Limpiar y formatear');
      const texto = (aviso?.paragraphs.join(' ') ?? '').toLowerCase();
      // Sin esto el botón se comería los topics del developer sin que nadie lo diga.
      expect(texto).toContain('se borra');
      expect(texto).toContain('topics');
      // Y todavía no se limpió nada: preguntar es parte de la operación.
      expect(calls.kafkaCleanCalls).toBe(0);
    });

    it('confirmar ejecuta la limpieza y el aviso se cierra', async () => {
      calls.answer = { ...calls.answer, resources: [kafkaBloqueada()] };
      const { comp, fixture } = await montar();

      comp.askFix(kafkaBloqueada());
      fixture.detectChanges();
      comp.confirmNotice();
      await new Promise((r) => setTimeout(r, 0));
      fixture.detectChanges();

      expect(calls.kafkaCleanCalls).toBe(1);
      expect(comp.notice()).toBeNull();
    });

    it('cerrar el aviso no limpia nada', async () => {
      const { comp, fixture } = await montar();

      comp.askFix(kafkaBloqueada());
      fixture.detectChanges();
      comp.dismissNotice();
      fixture.detectChanges();

      expect(calls.kafkaCleanCalls).toBe(0);
      expect(comp.notice()).toBeNull();
    });

    it('una limpieza que falla va al modal de error, no a la fila', async () => {
      calls.answer = { ...calls.answer, resources: [kafkaBloqueada()] };
      calls.kafkaCleanError = new Error('Error formateando storage');
      const { comp, fixture } = await montar();

      await comp.runFix(kafkaBloqueada());
      fixture.detectChanges();

      expect(comp.notice()?.title).toBe('No se pudo limpiar');
      expect(comp.notice()?.paragraphs.join(' ')).toContain('Error formateando storage');
    });
  });

  // --- el error de la conexión ----------------------------------------------

  it('la caja de error aparece y se va sola', async () => {
    const { comp, fixture } = await montar();

    emiteCaida(calls);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.error-box')).not.toBeNull();

    emiteEstado(calls);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.error-box')).toBeNull();
    expect(comp.readError()).toBe('');
  });
});
