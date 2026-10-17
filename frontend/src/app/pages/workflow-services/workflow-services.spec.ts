/**
 * Servicios con destino propio.
 *
 * Es la única de las tres páginas de tarjetas que muestra los dos puertos, y esa
 * es toda la diferencia de layout. Lo que se verifica es el filtro de grupo y que
 * el `portMode` siga llegando a la grilla compartida.
 */
import { TestBed } from '@angular/core/testing';

import { WorkflowServicesPage } from './workflow-services';
import {
  WorkflowCalls, callsVacios, kafkaArriba, recurso, workflowProviders,
} from '../../core/workflow-doubles';

describe('WorkflowServicesPage', () => {
  let calls: WorkflowCalls;

  beforeEach(async () => {
    calls = callsVacios();
    await TestBed.configureTestingModule({
      imports: [WorkflowServicesPage],
      providers: workflowProviders(calls),
    }).compileComponents();
  });

  async function montar() {
    const fixture = TestBed.createComponent(WorkflowServicesPage);
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

  it('dibuja sólo los servicios con destino propio', async () => {
    calls.answer = {
      ...calls.answer,
      resources: [
        recurso({ key: 'bastion', group: 'named', name: 'Bastion' }),
        kafkaArriba(),
        recurso({ key: 'db', group: 'db', name: 'Tunnel Aurora' }),
      ],
    };
    const { el } = await montar();

    expect(nombres(el)).toEqual(['Bastion']);
  });

  it('muestra el puerto remoto y el local, porque el servicio traduce uno en otro', async () => {
    calls.answer = { ...calls.answer, resources: [recurso({ remote_port: 9091, local_port: 3000 })] };
    const { el } = await montar();

    expect(el.querySelector('.res-card__port')?.textContent).toBe(':9091 → :3000');
  });

  it('el aviso de que "stop tunnel" mata sesiones de otro proyecto está siempre', async () => {
    // Es la advertencia que justifica el borde punteado. Sin ella, el
    // `foreign-tag` es un detalle que nadie sabe interpretar.
    const { el } = await montar();

    expect(el.textContent).toContain('mata todas las sesiones SSM');
  });

  it('el resumen cuenta sólo los servicios con destino propio', async () => {
    calls.answer = {
      ...calls.answer,
      resources: [
        recurso({ key: 'bastion', group: 'named', name: 'Bastion', state: 'up', pid: 1 }),
        recurso({ key: 'db', group: 'db', name: 'Tunnel Aurora', state: 'up', pid: 2 }),
      ],
    };
    const { comp } = await montar();

    expect(comp.summary()).toBe('1 arriba');
  });

  it('"Ver logs" de una fila abre el panel con el pid de esa fila', async () => {
    calls.answer = {
      ...calls.answer,
      resources: [recurso({ state: 'up', pid: 5150, log_file: 'bastion-5150.log' })],
    };
    const { comp, el } = await montar();

    const verLogs = [...el.querySelectorAll<HTMLButtonElement>('.res-card button')].find(
      (b) => b.textContent?.trim() === 'Ver logs',
    );
    verLogs?.click();
    await new Promise((r) => setTimeout(r, 0));

    expect(comp.logs()?.scope()).toBe(5150);
    expect(calls.logCalls).toEqual([{ pid: 5150, offset: 0 }]);
  });
});
