/**
 * La lista de sesiones: filtro y paginación juntos.
 *
 * El riesgo de esta página es uno concreto y silencioso: **filtrar después de
 * paginar**. Si el filtro corriera sobre las 25 filas de la página actual, buscar
 * "release" daría un resultado distinto según dónde estés parado, y no habría
 * ningún error —solo una lista que no es la que se buscó. Estos tests fijan que el
 * filtro va primero.
 */
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { SessionsPage } from './sessions';
import { SessionService } from '../core/services/session.service';
import { SessionSummaryInfo } from '../api-gen/models';

function session(n: number, title?: string): SessionSummaryInfo {
  return {
    id: `sesion-${n}`,
    title: title ?? `release/REP-${String(n).padStart(6, '0')}`,
    env_a: 'us-west-1',
    env_b: 'us-east-1',
    item_count: n,
    status_counts: { aplicado: 0, revisado: 0, pendiente: n },
    created_at: '2026-10-01T12:00:00Z',
  } as unknown as SessionSummaryInfo;
}

/** 40 `release/` y 20 `hotfix/`: un filtro por prefijo deja un subconjunto real. */
const MUCHAS = Array.from({ length: 60 }, (_, i) =>
  session(i + 1, i < 40 ? `release/REP-${String(i + 1).padStart(6, '0')}` : `hotfix/${i + 1}`),
);

describe('SessionsPage filtro + paginación', () => {
  async function setup(sesiones: SessionSummaryInfo[]): Promise<{
    fixture: ComponentFixture<SessionsPage>;
    comp: any;
    el: HTMLElement;
  }> {
    await TestBed.configureTestingModule({
      imports: [SessionsPage],
      providers: [
        provideRouter([]),
        {
          provide: SessionService,
          useValue: {
            list: () => Promise.resolve({ sessions: sesiones }),
            delete: () => Promise.resolve({}),
          },
        },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(SessionsPage);
    const comp = fixture.componentInstance as any;
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return { fixture, comp, el: fixture.nativeElement as HTMLElement };
  }

  async function settle(fixture: ComponentFixture<SessionsPage>): Promise<void> {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  }

  const filas = (el: HTMLElement) => el.querySelectorAll('table.sessions-table tbody tr').length;

  it('con muchas sesiones muestra una sola página', async () => {
    const { el } = await setup(MUCHAS);

    expect(filas(el)).toBe(25);
    expect(el.querySelector('.table-pagination')?.textContent).toContain('1–25 de 60');
  });

  it('el filtro cuenta sobre la lista entera, no sobre la página', async () => {
    const { comp, fixture } = await setup(MUCHAS);

    // "hotfix/60" está en la fila 60, o sea en la última página. Un filtro que
    // corriera sobre las 25 filas visibles no la encontraría.
    comp.filterText.set('hotfix/60');
    await settle(fixture);

    expect(comp.filteredSessions()).toHaveLength(1);
    expect(comp.page.total()).toBe(1);
  });

  it('filtrar devuelve a la primera página en vez de mostrar una tabla vacía', async () => {
    const { comp, fixture, el } = await setup(MUCHAS);

    comp.page.goTo(3);
    await settle(fixture);
    expect(comp.page.current()).toBe(3);

    comp.filterText.set('hotfix/60');
    await settle(fixture);

    // Sin el reset, la barra quedaría diciendo "Página 3 de 1" con el cuerpo vacío.
    expect(comp.page.current()).toBe(1);
    expect(filas(el)).toBe(1);
    expect(el.textContent).toContain('hotfix/60');
  });

  it('la paginación cuenta la lista filtrada, no la entera', async () => {
    const { comp, fixture, el } = await setup(MUCHAS);

    comp.filterText.set('release/');
    await settle(fixture);

    // 40 de 60: si la barra dijera 60 el usuario pensaría que hay 20 filas
    // escondidas que el filtro no agarró.
    expect(comp.filteredSessions()).toHaveLength(40);
    expect(comp.page.total()).toBe(40);
    expect(el.querySelector('.table-pagination')?.textContent).toContain('de 40');
    expect(filas(el)).toBe(25);
  });

  it('con pocas sesiones no aparece la barra', async () => {
    const { el } = await setup(MUCHAS.slice(0, 3));

    expect(filas(el)).toBe(3);
    expect(el.querySelector('.table-pagination')).toBeNull();
  });

  it('avanzar trae las sesiones siguientes, no las mismas', async () => {
    const { comp, fixture, el } = await setup(MUCHAS);

    const primera = [...el.querySelectorAll('.session-table-title')].map((t) =>
      t.textContent?.trim(),
    );

    comp.page.next();
    await settle(fixture);

    const segunda = [...el.querySelectorAll('.session-table-title')].map((t) =>
      t.textContent?.trim(),
    );
    expect(segunda[0]).not.toBe(primera[0]);
    expect(comp.page.current()).toBe(2);
  });
});
