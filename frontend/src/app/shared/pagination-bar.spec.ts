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

  it('sin filas no dibuja nada', async () => {
    const { fixture } = await setup(0, 25);

    expect(el(fixture).querySelector('.table-pagination')).toBeNull();
  });

  it('con una sola página deja el tamaño editable', async () => {
    // El caso que motivó el cambio: con 7 filas no hay paginación, pero "una sola
    // página" de 25 filas puede seguir desbordando la pantalla, y sin selector no
    // hay forma de bajarla.
    const { fixture, host } = await setup(7, 25);

    const bar = el(fixture).querySelector('.table-pagination');
    expect(bar).not.toBeNull();
    expect(el(fixture).querySelector('.table-pagination__size select')).not.toBeNull();

    // Y bajarla a 5 parte la tabla, que es justo lo que se quería.
    const select = el(fixture).querySelector('.table-pagination__size select') as HTMLSelectElement;
    select.value = '5';
    select.dispatchEvent(new Event('change'));
    fixture.detectChanges();

    expect(host.p.pageSize()).toBe(5);
    expect(host.p.pageCount()).toBe(2);
    expect(el(fixture).querySelector('.table-pagination')?.textContent).toContain('1–5 de 7');
  });

  it('con una sola página no inventa navegación', async () => {
    // "Página 1 de 1" y dos botones que nunca se encienden son ruido.
    const { fixture } = await setup(25, 25);

    const bar = el(fixture).querySelector('.table-pagination');
    expect(bar).not.toBeNull();
    expect(bar?.textContent).toContain('1–25 de 25');
    expect(bar?.querySelectorAll('button').length).toBe(0);
    expect(bar?.textContent).not.toContain('Página');
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

  it('la navegación desaparece si la lista se reduce a una página', async () => {
    const { fixture, host } = await setup(60, 25);

    host.items.set(['una']);
    fixture.detectChanges();

    // La barra sigue —el tamaño se puede cambiar— pero sin botones ni etiqueta de
    // página: ya no hay a dónde ir.
    expect(el(fixture).querySelector('.table-pagination')).not.toBeNull();
    expect(el(fixture).querySelectorAll('.table-pagination button').length).toBe(0);
    expect(el(fixture).querySelector('.table-pagination')?.textContent).not.toContain('Página');
  });
});
