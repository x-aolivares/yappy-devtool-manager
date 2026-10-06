/**
 * La barra de paginación en el DOM.
 *
 * Lo que importa acá es una sola cosa: **la barra no aparece si no hay nada que
 * paginar**. Con "Página 1 de 1" y dos botones apagados la barra informa que no
 * tiene nada que informar, y en las tablas chicas —las de params-create, que son
 * una fila por región— sería ruido permanente encima de la tabla.
 */
import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { PaginationBarComponent } from './pagination-bar';
import { paginate } from './paginate';

@Component({
  imports: [PaginationBarComponent],
  template: `<app-pagination-bar [p]="p" />`,
})
class Host {
  readonly items = signal<string[]>([]);
  readonly p = paginate(() => this.items());
}

describe('PaginationBarComponent', () => {
  async function setup(n: number, size = 25): Promise<{ fixture: ComponentFixture<Host>; host: Host }> {
    await TestBed.configureTestingModule({ imports: [Host] }).compileComponents();
    const fixture = TestBed.createComponent(Host);
    const host = fixture.componentInstance;
    host.items.set(Array.from({ length: n }, (_, i) => `fila ${i}`));
    host.p.setPageSize(size);
    fixture.detectChanges();
    return { fixture, host };
  }

  const el = (f: ComponentFixture<Host>) => f.nativeElement as HTMLElement;

  it('con una sola página no dibuja nada', async () => {
    const { fixture } = await setup(3, 25);

    expect(el(fixture).querySelector('.table-pagination')).toBeNull();
  });

  it('con una página exacta tampoco: no hay nada que recorrer', async () => {
    const { fixture } = await setup(25, 25);

    expect(el(fixture).querySelector('.table-pagination')).toBeNull();
  });

  it('sin filas tampoco', async () => {
    const { fixture } = await setup(0, 25);

    expect(el(fixture).querySelector('.table-pagination')).toBeNull();
  });

  it('con más de una página muestra el rango y los controles', async () => {
    const { fixture } = await setup(60, 25);

    const bar = el(fixture).querySelector('.table-pagination');
    expect(bar).not.toBeNull();
    expect(bar?.textContent).toContain('1–25 de 60');
    expect(bar?.textContent).toContain('Página 1 de 3');
    expect(bar?.querySelectorAll('button').length).toBe(2);
  });

  it('"Anterior" arranca apagado y "Siguiente" encendido', async () => {
    const { fixture } = await setup(60, 25);
    const botones = (): HTMLButtonElement[] => [
      ...el(fixture).querySelectorAll('.table-pagination button'),
    ] as HTMLButtonElement[];

    expect(botones()[0].disabled).toBe(true);
    expect(botones()[1].disabled).toBe(false);

    botones()[1].click();
    fixture.detectChanges();

    expect(botones()[0].disabled).toBe(false);
    expect(el(fixture).querySelector('.table-pagination')?.textContent).toContain('Página 2 de 3');
  });

  it('"Anterior" se apaga en la última página', async () => {
    const { fixture, host } = await setup(60, 25);

    host.p.goTo(3);
    fixture.detectChanges();

    const [anterior, siguiente] = [...el(fixture).querySelectorAll('.table-pagination button')] as HTMLButtonElement[];
    expect(anterior.disabled).toBe(false);
    expect(siguiente.disabled).toBe(true);
  });

  it('el selector de filas cambia el tamaño de página', async () => {
    const { fixture, host } = await setup(60, 25);

    const select = el(fixture).querySelector('.table-pagination select') as HTMLSelectElement;
    expect(select).not.toBeNull();
    // El label va adentro porque el `<select>` es pelado: la regla del
    // `.field-label` es sólo para cuando el control no trae etiqueta propia.
    expect(el(fixture).querySelector('.table-pagination__size')?.textContent).toContain('Filas');

    select.value = '50';
    select.dispatchEvent(new Event('change'));
    fixture.detectChanges();

    expect(host.p.pageSize()).toBe(50);
    expect(el(fixture).querySelector('.table-pagination')?.textContent).toContain('1–50 de 60');
  });

  it('la barra desaparece si la lista se reduce a una página', async () => {
    const { fixture, host } = await setup(60, 25);

    host.items.set(['una']);
    fixture.detectChanges();

    expect(el(fixture).querySelector('.table-pagination')).toBeNull();
  });
});
