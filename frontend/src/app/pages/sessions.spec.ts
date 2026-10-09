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
  /**
   * `borradas` recibe los ids que pasaron por `SessionService.delete`. Es como se
   * prueba que el borrado ocurrió sin volver a armar el módulo de testing en cada
   * test: `setup` es compartida y `remove()` ya no borra al clickear.
   */
  let borradas: string[] = [];

  async function setup(sesiones: SessionSummaryInfo[]): Promise<{
    fixture: ComponentFixture<SessionsPage>;
    comp: any;
    el: HTMLElement;
  }> {
    borradas = [];
    await TestBed.configureTestingModule({
      imports: [SessionsPage],
      providers: [
        provideRouter([]),
        {
          provide: SessionService,
          useValue: {
            list: () => Promise.resolve({ sessions: sesiones }),
            delete: (id: string) => {
              borradas.push(id);
              return Promise.resolve({});
            },
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

  /** El botón "Eliminar" de la fila `n`. */
  const botonEliminar = (el: HTMLElement, n: number): HTMLButtonElement =>
    [...el.querySelectorAll<HTMLButtonElement>('button.secondary')].filter((b) =>
      (b.textContent ?? '').includes('Eliminar'),
    )[n];

  /**
   * Click en el botón de confirmar del `NoticeModal`.
   *
   * `remove()` ya no borra: deja la sesión pendiente y abre el modal. El click va
   * por el `output` `confirmed`, o sea por el mismo camino que el del usuario. No
   * hay `sleep`: el modal es un signal y el click es síncrono.
   */
  function confirmarEnModal(el: HTMLElement): void {
    const boton = el.querySelector<HTMLButtonElement>('.notice-modal__confirm');
    if (!boton) throw new Error('No hay modal de confirmación abierto');
    boton.click();
  }

  it('eliminar una sesión pide confirmación y recién ahí borra', async () => {
    const { fixture, el } = await setup(MUCHAS.slice(0, 3));

    botonEliminar(el, 1).click();
    await settle(fixture);

    // El click sólo abre el modal. El texto es lo que dice qué va a pasar, que es
    // justo lo que el `confirm()` del navegador no decía.
    expect(borradas).toEqual([]);
    const texto = el.querySelector('.notice-modal')?.textContent ?? '';
    expect(texto).toContain('DEFINITIVAMENTE');
    expect(texto).toContain('No se puede deshacer');

    confirmarEnModal(el);
    await settle(fixture);

    expect(borradas).toEqual(['sesion-2']);
    expect(el.querySelector('.notice-modal')).toBeNull();
  });

  it('cancelar la confirmación no borra la sesión', async () => {
    const { fixture, el } = await setup(MUCHAS.slice(0, 3));

    botonEliminar(el, 0).click();
    await settle(fixture);

    el.querySelector<HTMLButtonElement>('.notice-modal__cancel')!.click();
    await settle(fixture);

    expect(borradas).toEqual([]);
    expect(el.querySelector('.notice-modal')).toBeNull();
  });

  it('el modal de eliminar es destructivo: el botón de confirmar va con tono de peligro', async () => {
    const { fixture, el } = await setup(MUCHAS.slice(0, 2));

    botonEliminar(el, 0).click();
    await settle(fixture);

    const confirmar = el.querySelector<HTMLButtonElement>('.notice-modal__confirm')!;
    expect(confirmar.classList).toContain('notice-modal__confirm--danger');
    // Y el título va en la tinta de error: es lo único que no se puede deshacer.
    expect(el.querySelector('.notice-modal--danger')).not.toBeNull();
  });

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

  it('con pocas sesiones la barra no navega pero el tamaño sigue editable', async () => {
    const { el } = await setup(MUCHAS.slice(0, 3));

    expect(filas(el)).toBe(3);
    // Con 3 sesiones no hay nada que recorrer, pero el selector queda: una sola
    // "página" puede seguir desbordando la pantalla.
    expect(el.querySelector('.table-pagination__size select')).not.toBeNull();
    expect(el.querySelectorAll('.table-pagination button').length).toBe(0);
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
