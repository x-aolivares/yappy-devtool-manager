/**
 * Base de datos.
 *
 * Es la sección más chica de las cuatro —el backend manda una sola fila de
 * `group: 'db'`— pero tiene su propia página porque es la que se mira cuando la
 * base no levanta. Lo que se verifica es el filtro de grupo, que es lo único que
 * la puede hacer mostrar el recurso equivocado.
 */
import { TestBed } from '@angular/core/testing';

import { WorkflowDatabasePage } from './workflow-database';
import {
  WorkflowCalls, callsVacios, kafkaArriba, recurso, workflowProviders,
} from '../../core/workflow-doubles';

describe('WorkflowDatabasePage', () => {
  let calls: WorkflowCalls;

  beforeEach(async () => {
    calls = callsVacios();
    await TestBed.configureTestingModule({
      imports: [WorkflowDatabasePage],
      providers: workflowProviders(calls),
    }).compileComponents();
  });

  async function montar() {
    const fixture = TestBed.createComponent(WorkflowDatabasePage);
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

  it('dibuja sólo el grupo db', async () => {
    calls.answer = {
      ...calls.answer,
      resources: [
        recurso({ key: 'db', group: 'db', name: 'Tunnel Aurora' }),
        recurso({ key: 'bastion', group: 'named', name: 'Bastion' }),
        kafkaArriba(),
      ],
    };
    const { el } = await montar();

    expect(nombres(el)).toEqual(['Tunnel Aurora']);
  });

  it('muestra sólo el puerto local: un túnel a Aurora no traduce nada', async () => {
    calls.answer = {
      ...calls.answer,
      resources: [recurso({ key: 'db', group: 'db', name: 'Tunnel Aurora', remote_port: 3306, local_port: 3306 })],
    };
    const { el } = await montar();

    expect(el.querySelector('.res-card__port')?.textContent).toBe(':3306');
  });

  it('sin túnel levantado dice que las demás herramientas no van a contestar', async () => {
    // El vacío de esta página no es un vacío neutro: sin el túnel, Migrar datos
    // y Ejecutar SQL fallan. Decirlo es la diferencia entre un mensaje y un rato
    // de buscar el error en otro lado.
    const { el } = await montar();

    expect(el.querySelector('.res-grid__empty')?.textContent).toContain('Migrar datos');
  });

  it('"Ver logs" de la fila abre el panel con el pid de esa fila', async () => {
    calls.answer = {
      ...calls.answer,
      resources: [
        recurso({ key: 'db', group: 'db', name: 'Tunnel Aurora', state: 'up', pid: 97, log_file: 'db-97.log' }),
      ],
    };
    const { comp, el } = await montar();

    const verLogs = [...el.querySelectorAll<HTMLButtonElement>('.res-card button')].find(
      (b) => b.textContent?.trim() === 'Ver logs',
    );
    verLogs?.click();
    await new Promise((r) => setTimeout(r, 0));

    expect(comp.logs()?.scope()).toBe(97);
    expect(calls.logCalls).toEqual([{ pid: 97, offset: 0, section: 'database' }]);
  });
});
