/**
 * El dropdown de repositorios.
 *
 * Es un `<datalist>` reemplazado por un `role="listbox"` propio, así que lo que
 * este archivo fija es el comportamiento que el nativo no daba: dónde se abre,
 * cómo se navega con el teclado, qué significa `Enter` con y sin fila marcada,
 * y que escribir no se coma la fila que estaba resaltada.
 */
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { RepoPicker } from './repo-picker';

const REPOS = [
  'yappy-a',
  'yappy-b',
  'yappy-trnxd-backend-refund-service',
  'yappy-trnxd-frontend-web',
];

describe('RepoPicker', () => {
  // Se asignan en `abrir()`, que es el único que arma el componente: el `!` es
  // para que el compilador lo acepte, no un null disfrazado.
  let fixture!: ComponentFixture<RepoPicker>;
  let comp!: RepoPicker;
  let el!: HTMLElement;
  let input!: HTMLInputElement;
  let submits: number;

  async function abrir(options: string[] = REPOS): Promise<void> {
    fixture = TestBed.createComponent(RepoPicker);
    fixture.componentRef.setInput('options', options);
    comp = fixture.componentInstance;
    submits = 0;
    comp.submit.subscribe(() => submits++);
    fixture.detectChanges();
    await fixture.whenStable();
    el = fixture.nativeElement as HTMLElement;
    input = el.querySelector<HTMLInputElement>('input')!;
    // Enfocar abre: es lo que hace el navegador con un combobox y lo que hace
    // que las sugerencias se vean sin tener que escribir primero.
    input.dispatchEvent(new Event('focus'));
    fixture.detectChanges();
  }

  function opciones(): string[] {
    return [...el.querySelectorAll('[role="option"]')].map((o) => o.textContent!.trim());
  }

  function escribir(texto: string): void {
    input.value = texto;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    fixture.detectChanges();
  }

  function tecla(key: string): void {
    input.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    fixture.detectChanges();
  }

  function listbox(): HTMLElement | null {
    return el.querySelector('[role="listbox"]');
  }

  it('al enfocar se abre y ofrece todos los repos', async () => {
    await abrir();

    expect(listbox()).not.toBeNull();
    expect(opciones()).toEqual(REPOS);
    expect(input.getAttribute('aria-expanded')).toBe('true');
    expect(el.querySelector('.status')!.textContent).toContain('4');
  });

  it('filtra por el texto escrito, y no sólo por el principio del nombre', async () => {
    await abrir();
    escribir('refund');

    // "refund" no empieza ningún nombre: está en el medio de uno.
    expect(opciones()).toEqual(['yappy-trnxd-backend-refund-service']);
    expect(el.querySelector('.status')!.textContent).toContain('refund');
  });

  it('sin coincidencias lo dice en vez de mostrar una lista vacía', async () => {
    await abrir();
    escribir('no-existe-ninguno');

    expect(opciones()).toEqual([]);
    expect(el.querySelector('.empty')!.textContent).toContain('Sin coincidencias');
  });

  it('no ofrece más de 50 aunque el workspace tenga ochocientos', async () => {
    const muchos = Array.from({ length: 800 }, (_, i) => `repo-${String(i).padStart(3, '0')}`);
    await abrir(muchos);

    expect(opciones().length).toBe(50);
  });

  it('Enter sin fila marcada emite submit, que es "Consultar"', async () => {
    await abrir();
    escribir('refund');

    tecla('Enter');

    expect(submits).toBe(1);
    expect(listbox()).toBeNull();
    // Elegir no es consultar: el valor quedó escrito y falta el botón.
    expect(comp.value()).toBe('refund');
  });

  it('ArrowDown marca la primera fila y Enter la elige', async () => {
    await abrir();

    tecla('ArrowDown');
    expect(el.querySelector('.option.is-active')!.textContent).toContain('yappy-a');

    tecla('Enter');

    expect(comp.value()).toBe('yappy-a');
    expect(submits).toBe(0);
    expect(listbox()).toBeNull();
  });

  it('ArrowDown no se pasa del final de la lista', async () => {
    await abrir();

    for (let i = 0; i < 20; i++) tecla('ArrowDown');

    expect(opciones()[comp.activeIndex()]).toContain('yappy-trnxd-frontend-web');
    expect(comp.activeIndex()).toBe(REPOS.length - 1);
  });

  it('escribir borra la fila marcada, para que siga significando consultar', async () => {
    await abrir();
    tecla('ArrowDown');

    escribir('refund');
    tecla('Enter');

    // Sin esto, Enter elegiría la primera fila de la lista anterior.
    expect(submits).toBe(1);
    expect(comp.value()).toBe('refund');
  });

  it('Escape cierra sin emitir nada', async () => {
    await abrir();

    tecla('Escape');

    expect(listbox()).toBeNull();
    expect(input.getAttribute('aria-expanded')).toBe('false');
    expect(submits).toBe(0);
  });

  it('elegir con el mouse escribe el valor y cierra', async () => {
    await abrir();

    el.querySelector('[role="option"]')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    fixture.detectChanges();

    expect(comp.value()).toBe('yappy-a');
    expect(listbox()).toBeNull();
  });

  it('el dropdown se cierra al perder el foco', async () => {
    await abrir();

    input.dispatchEvent(new Event('blur'));
    fixture.detectChanges();

    expect(listbox()).toBeNull();
  });

  it('el id del input es el que apunta el label de la pantalla', async () => {
    await abrir();

    expect(input.id).toBe('repo');
    expect(listbox()!.id).toBe('repo-list');
    expect(input.getAttribute('aria-controls')).toBe('repo-list');
  });
});
