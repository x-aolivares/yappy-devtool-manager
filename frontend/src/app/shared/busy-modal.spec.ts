import { ComponentFixture, TestBed } from '@angular/core/testing';
import { BusyModalComponent } from './busy-modal';

describe('BusyModalComponent', () => {
  let fixture: ComponentFixture<BusyModalComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [BusyModalComponent] }).compileComponents();
    fixture = TestBed.createComponent(BusyModalComponent);
  });

  const host = (): HTMLElement => fixture.nativeElement as HTMLElement;

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

  it('no ofrece forma de cerrarlo: la operación sigue corriendo', () => {
    fixture.componentRef.setInput('open', true);
    fixture.detectChanges();

    // Sin botón, sin acción de cierre y sin Escape: un backdrop que cerrara un
    // modal de espera mentiría sobre lo que está pasando.
    expect(host().querySelector('button')).toBeNull();
    expect(host().querySelector('.busy-modal__x')).toBeNull();

    // El backdrop es un div pelado: ni click, ni teclado. Si alguna vez aparece un
    // `(click)` acá, es la operación perdiéndose y hay que revisarlo.
    const backdrop = host().querySelector('.busy-modal__backdrop');
    expect(backdrop).not.toBeNull();
    expect(backdrop?.tagName).toBe('DIV');
    expect(backdrop?.hasAttribute('tabindex')).toBe(false);
    expect((backdrop as HTMLElement & { onclick: unknown }).onclick).toBeNull();
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
});