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
  WorkflowCalls, callsVacios, kafkaArriba, recurso, workflowProviders,
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
    it('el poll va sin ambiente ni cluster', async () => {
      await montar();

      expect(calls.stateCalls).toEqual([{}]);
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
      expect(calls.logCalls).toEqual([{ pid: 21224, offset: 0 }]);
    });

    it('una fila ajena no se puede parar desde la tarjeta', async () => {
      calls.answer = { ...calls.answer, resources: [kafkaArriba({ foreign: true })] };
      const { el } = await montar();

      const card = tarjeta(el, 'Kafdrop UI');
      expect(card.classList).toContain('res-card--foreign');
      expect([...card.querySelectorAll('button')].map((b) => b.textContent?.trim())).not.toContain('Parar');
    });
  });

  // --- el error del poll ----------------------------------------------------

  it('la caja de error aparece y se va sola', async () => {
    const { comp, fixture } = await montar();

    comp.settleFailureForTest({ status: 0 });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.error-box')).not.toBeNull();

    comp.settleFailureForTest(undefined);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.error-box')).toBeNull();
  });
});
