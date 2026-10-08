import { ComponentFixture, TestBed } from '@angular/core/testing';
import { BusyModalComponent } from './busy-modal';

describe('BusyModalComponent', () => {
  let fixture: ComponentFixture<BusyModalComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [BusyModalComponent] }).compileComponents();
    fixture = TestBed.createComponent(BusyModalComponent);
  });

  const host = (): HTMLElement => fixture.nativeElement as HTMLElement;
  const button = (): HTMLButtonElement | null =>
    host().querySelector<HTMLButtonElement>('.busy-modal__cancel');

  it('no muestra nada mientras está cerrado', () => {
    fixture.componentRef.setInput('open', false);
    fixture.detectChanges();

    expect(host().querySelector('.busy-modal')).toBeNull();
    expect(host().querySelector('.busy-modal__backdrop')).toBeNull();
  });

  it('muestra el mensaje y el spinner cuando está abierto', () => {
    fixture.componentRef.setInput('open', true);
    fixture.componentRef.setInput('message', 'Buscando…');
    fixture.detectChanges();

    expect(host().querySelector('.busy-modal__text')?.textContent).toContain('Buscando…');
    expect(host().querySelector('.spinner')).not.toBeNull();
  });

  it('anuncia el texto con role="status" en vez de tomar el foco', () => {
    fixture.componentRef.setInput('open', true);
    fixture.detectChanges();

    // El dialog no existe con role="dialog": el texto se anuncia por aria-live
    // para no mover el foco de donde quedó, que en una búsqueda es el input.
    const dialog = host().querySelector('.busy-modal');
    expect(dialog?.getAttribute('role')).toBe('status');
    expect(dialog?.getAttribute('aria-live')).toBe('polite');
    expect(dialog?.getAttribute('aria-modal')).toBeNull();
  });

  it('el backdrop sigue sin cerrar nada: el botón es el único que corta', () => {
    fixture.componentRef.setInput('open', true);
    fixture.detectChanges();

    // Sin Escape ni click en el fondo: un backdrop que cerrara un modal de
    // espera mentiría sobre lo que está pasando. Lo que cierra es el botón.
    const backdrop = host().querySelector('.busy-modal__backdrop');
    expect(backdrop).not.toBeNull();
    expect(backdrop?.tagName).toBe('DIV');
    expect(backdrop?.hasAttribute('tabindex')).toBe(false);
    expect((backdrop as HTMLElement & { onclick: unknown }).onclick).toBeNull();

    // Y no es el botón de cerrar del notice-modal: el que corta es el de cancelar
    // la operación, y dice lo que hace.
    expect(host().querySelector('.busy-modal__x')).toBeNull();
    expect(button()?.textContent?.trim()).toBe('Cancelar');
  });

  it('el click emite "cancelled" una vez por click', () => {
    let veces = 0;
    fixture.componentInstance.cancelled.subscribe(() => veces++);
    fixture.componentRef.setInput('open', true);
    fixture.detectChanges();

    button()!.click();
    button()!.click();
    expect(veces).toBe(2);

    // Cerrado, no hay nada que cortar.
    fixture.componentRef.setInput('open', false);
    fixture.detectChanges();
    expect(button()).toBeNull();
  });

  it('una operación que escribe no dice "Cancelar": dice "Dejar de esperar"', () => {
    fixture.componentRef.setInput('open', true);
    fixture.componentRef.setInput('writes', true);
    fixture.detectChanges();

    // "Cancelar" sobre un `REPLACE INTO` en vuelo promete una cosa falsa: que no
    // se escribió. El rótulo dice lo que el botón hace —dejar de esperar— y la
    // línea de abajo dice lo que no puede garantizar.
    expect(button()?.textContent?.trim()).toBe('Dejar de esperar');
    expect(host().querySelector('.busy-modal__hint')?.textContent).toContain(
      'puede igual terminar',
    );
  });

  it('una lectura no lleva la aclaración: "Cancelar" es literal', () => {
    fixture.componentRef.setInput('open', true);
    fixture.detectChanges();

    expect(host().querySelector('.busy-modal__hint')).toBeNull();
  });

  it('marca el spinner como decorativo para que no se anuncie dos veces', () => {
    fixture.componentRef.setInput('open', true);
    fixture.detectChanges();

    expect(host().querySelector('.spinner')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('usa un texto por defecto si no le pasan message', () => {
    fixture.componentRef.setInput('open', true);
    fixture.detectChanges();

    expect(host().querySelector('.busy-modal__text')?.textContent?.trim()).toBe('Trabajando…');
  });

  it('devuelve el foco al elemento que lo tenía cuando se abre', async () => {
    // El caso de quien tabuló hasta "Cancelar": ese elemento se va con el modal y
    // sin esto el foco caería al body, que obliga a recorrer la página de nuevo.
    const trigger = document.createElement('button');
    document.body.appendChild(trigger);
    trigger.focus();

    fixture.componentRef.setInput('open', true);
    fixture.detectChanges();
    expect(document.activeElement).toBe(trigger);

    fixture.componentRef.setInput('open', false);
    fixture.detectChanges();
    await fixture.whenStable();

    expect(document.activeElement).toBe(trigger);
    trigger.remove();
  });
});