/**
 * El panel del Kafka Agent.
 *
 * Lo que se prueba acá es lo que lo diferencia de la tarjeta del recurso: que
 * las cinco operaciones golpean las cinco rutas del contrato original y que la
 * prueba local muestra *qué paso falló*, no un 500 sin detalle. El start/stop se
 * prueba en la página, porque ahí vive el `busy` compartido.
 */
import { ComponentFixture, TestBed } from '@angular/core/testing';

import { KafkaAgentPanel } from './kafka-agent-panel';
import {
  WorkflowCalls, callsVacios, kafkaAgente, workflowProviders,
} from '../../core/workflow-doubles';

describe('KafkaAgentPanel', () => {
  let calls: WorkflowCalls;

  beforeEach(async () => {
    calls = callsVacios();
    await TestBed.configureTestingModule({
      imports: [KafkaAgentPanel],
      providers: workflowProviders(calls),
    }).compileComponents();
  });

  async function montar(fila = kafkaAgente()) {
    const fixture = TestBed.createComponent(KafkaAgentPanel);
    fixture.componentRef.setInput('row', fila);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return { comp: fixture.componentInstance, el: fixture.nativeElement as HTMLElement, fixture };
  }

  function boton(el: HTMLElement, texto: string): HTMLButtonElement {
    const found = [...el.querySelectorAll<HTMLButtonElement>('button')]
      .find((b) => b.textContent?.trim() === texto);
    if (!found) throw new Error(`No está el botón ${texto}`);
    return found;
  }

  function tab(el: HTMLElement, texto: string): HTMLButtonElement {
    const found = [...el.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
      .find((b) => b.textContent?.trim() === texto);
    if (!found) throw new Error(`No está la pestaña ${texto}`);
    return found;
  }

  function input(el: HTMLElement, label: string): HTMLInputElement | HTMLTextAreaElement {
    const found = [...el.querySelectorAll<HTMLLabelElement>('label')]
      .find((l) => l.textContent?.includes(label))
      ?.querySelector<HTMLInputElement | HTMLTextAreaElement>('input, textarea');
    if (!found) throw new Error(`No está el campo ${label}`);
    return found;
  }

  function escribir(el: HTMLInputElement | HTMLTextAreaElement, valor: string): void {
    el.value = valor;
    el.dispatchEvent(new Event('input'));
  }

  async function ejecutar(el: HTMLElement, fixture: ComponentFixture<KafkaAgentPanel>): Promise<void> {
    boton(el, 'Ejecutar').click();
    await new Promise((r) => setTimeout(r, 0));
    fixture.detectChanges();
  }

  // --- el estado ---------------------------------------------------------------

  it('muestra el badge del tracker y el broker del /health', async () => {
    const { el } = await montar();

    expect(el.querySelector('.badge')?.textContent?.trim()).toBe('arriba');
    expect(el.textContent).toContain('127.0.0.1:9092');
  });

  it('deshabilita parar y reiniciar cuando el tracker dice que está abajo', async () => {
    const { el } = await montar(kafkaAgente({ state: 'down', pid: null }));

    expect(boton(el, 'Parar').disabled).toBe(true);
    expect(boton(el, 'Reiniciar').disabled).toBe(true);
    expect(boton(el, 'Levantar').disabled).toBe(false);
  });

  it('el aviso de agente abajo no aparece cuando está arriba', async () => {
    const { el } = await montar();

    expect(el.querySelector('.callout')?.textContent ?? '').not.toContain('no está arriba');
  });

  // --- start / stop / restart --------------------------------------------------

  it('los botones de proceso emiten, no llaman al backend directo', async () => {
    const { comp, el, fixture } = await montar();
    const started: string[] = [];
    const stopped: string[] = [];
    const restarted: string[] = [];
    comp.start.subscribe(() => started.push('start'));
    comp.stop.subscribe(() => stopped.push('stop'));
    comp.restart.subscribe(() => restarted.push('restart'));

    // Arriba: se puede parar y reiniciar, pero no levantar de nuevo.
    boton(el, 'Parar').click();
    boton(el, 'Reiniciar').click();
    // Abajo: el botón que aparece es el de levantar.
    fixture.componentRef.setInput('row', kafkaAgente({ state: 'down', pid: null }));
    fixture.detectChanges();
    boton(el, 'Levantar').click();

    expect(started).toEqual(['start']);
    expect(stopped).toEqual(['stop']);
    expect(restarted).toEqual(['restart']);
  });

  // --- las cinco operaciones ---------------------------------------------------

  it('publicar arma el CloudEvent con los campos del contrato original', async () => {
    const { el, fixture } = await montar();
    escribir(input(el, 'Cloud event id'), 'evt-42');
    escribir(input(el, 'Payload'), '{"foo": "bar"}');
    fixture.detectChanges();

    await ejecutar(el, fixture);

    expect(calls.agentCalls).toHaveLength(1);
    expect(calls.agentCalls[0].path).toBe('producer');
    expect(calls.agentCalls[0].body).toEqual({
      topic: 'yappy-agent-test',
      cloud_event_id: 'evt-42',
      type: 'com.yappy.evento',
      headers: {},
      message: { foo: 'bar' },
    });
    expect(el.querySelector('.agent-result')?.textContent).toContain('Publicacion exitosa');
  });

  it('un JSON inválido se dice antes de mandar nada', async () => {
    const { el, fixture } = await montar();
    escribir(input(el, 'Payload'), '{no es json');

    await ejecutar(el, fixture);

    expect(calls.agentCalls).toEqual([]);
    expect(el.querySelector('.callout')?.textContent).toContain('no es un JSON válido');
  });

  it('consumir v1 manda el cloud_event_id y consumir v2 el par header', async () => {
    const { el, fixture } = await montar();

    tab(el, 'Consumir v1').click();
    fixture.detectChanges();
    escribir(input(el, 'Cloud event id'), 'evt-v1');
    await ejecutar(el, fixture);

    tab(el, 'Consumir v2').click();
    fixture.detectChanges();
    escribir(input(el, 'Header key'), 'customheader');
    escribir(input(el, 'Header value'), 'valor1');
    await ejecutar(el, fixture);

    expect(calls.agentCalls.map((c) => c.path)).toEqual(['consumer', 'v2/consumer']);
    expect(calls.agentCalls[0].body).toEqual({
      cloud_event_id: 'evt-v1', topic: 'yappy-agent-test', max_polls: 3,
    });
    expect(calls.agentCalls[1].body).toEqual({
      header_key: 'customheader', header_value: 'valor1',
      topic: 'yappy-agent-test', max_polls: 3,
    });
  });

  it('escuchar y limpiar llegan a sus rutas, con la lista de tópicos partida', async () => {
    const { el, fixture } = await montar();

    tab(el, 'Escuchar').click();
    fixture.detectChanges();
    await ejecutar(el, fixture);

    tab(el, 'Limpiar tópicos').click();
    fixture.detectChanges();
    escribir(input(el, 'Tópicos'), ' topic-a, topic-b ');
    await ejecutar(el, fixture);

    expect(calls.agentCalls.map((c) => c.path)).toEqual(['listener', 'clean-topics']);
    expect(calls.agentCalls[1].body).toEqual({ body: { topics: ['topic-a', 'topic-b'] } });
  });

  it('un 404 del agente se muestra como error, no como resultado vacío', async () => {
    calls.agentError = new Error('Not Found');
    const { el, fixture } = await montar();

    await ejecutar(el, fixture);

    expect(el.querySelector('.agent-result')).toBeNull();
    expect(el.querySelector('.callout')?.textContent).toContain('Not Found');
  });

  // --- la prueba local ---------------------------------------------------------

  it('la prueba local muestra cada paso con su resultado', async () => {
    calls.agentReport = {
      ok: false,
      topic: 'yappy-agent-smoke',
      correlation_id: 'evt-test',
      messages: 1,
      total_ms: 12.5,
      checks: [
        { name: 'producer', ok: true, status: 200, detail: 'Publicación', elapsed_ms: 4.1 },
        { name: 'consumer', ok: false, status: 404, detail: 'No apareció', elapsed_ms: 8.4 },
      ],
    };
    const { el, fixture } = await montar();

    boton(el, 'Correr prueba').click();
    await new Promise((r) => setTimeout(r, 0));
    fixture.detectChanges();

    expect(calls.agentCalls.at(-1)?.path).toBe('tests/run');
    expect(calls.agentCalls.at(-1)?.body).toEqual({
      topic: 'yappy-agent-smoke', messages: 1, max_polls: 3, cleanup: false,
    });
    expect(el.querySelector('.agent-test-summary')?.textContent).toContain('Falló');
    const filas = [...el.querySelectorAll('tbody tr')].map((r) => r.textContent ?? '');
    expect(filas[0]).toContain('Publicar');
    expect(filas[1]).toContain('FALLÓ');
    expect(filas[1]).toContain('404');
  });

  it('si el agente está apagado, la prueba dice por qué no se pudo correr', async () => {
    calls.agentError = new Error('El Kafka Agent no está levantado');
    const { el, fixture } = await montar();

    boton(el, 'Correr prueba').click();
    await new Promise((r) => setTimeout(r, 0));
    fixture.detectChanges();

    expect(el.querySelector('.agent-tests .callout')?.textContent).toContain('no está levantado');
    expect(el.querySelector('.agent-test-summary')).toBeNull();
  });
});
