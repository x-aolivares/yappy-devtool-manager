/**
 * Modal de aviso, para lo que no cabe en la línea donde ocurre.
 *
 * Nace de una cosa concreta: un mensaje de error de una celda de tabla con doscientos
 * caracteres y el `border-left` de `.hint-error` —que es una regla de bloque,
 * pensada para un párrafo— rompe el layout de la tabla. Escalar el detalle a un
 * modal y dejar en la fila sólo un marcador corto deja la tabla legible y el
 * aviso completo donde se lo puede leer.
 *
 * Es genérico a propósito: el cuerpo va por `<ng-content>`, así que el mismo
 * modal sirve para un error de introspección, para una confirmación, o para
 * cualquier cosa que no entre en un renglón.
 *
 * Cerrar avisa con `closed`. No guarda nada: el estado es del que lo abre.
 */
import { Component, ElementRef, HostListener, effect, inject, input, output } from '@angular/core';

let seq = 0;

@Component({
  selector: 'app-notice-modal',
  template: `
    @if (open()) {
      <div class="notice-modal__backdrop" (click)="closed.emit()"></div>
      <div
        class="notice-modal"
        role="dialog"
        aria-modal="true"
        [attr.aria-labelledby]="titleId"
        #dialog
      >
        <div class="notice-modal__head">
          <h2 [id]="titleId">{{ title() }}</h2>
          <button
            type="button"
            class="notice-modal__x"
            aria-label="Cerrar"
            (click)="closed.emit()"
          >
            ✕
          </button>
        </div>
        <div class="notice-modal__body">
          <ng-content />
        </div>
        <div class="notice-modal__actions">
          <button type="button" (click)="closed.emit()">{{ confirmLabel() }}</button>
        </div>
      </div>
    }
  `,
})
export class NoticeModalComponent {
  readonly open = input(false);
  readonly title = input.required<string>();
  readonly confirmLabel = input('Entendido');
  readonly closed = output<void>();

  /** Único por instancia: dos modales a la vez no deben compartir `aria-labelledby`. */
  protected readonly titleId = `notice-modal-${++seq}`;

  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  constructor() {
    // El diálogo recibe el foco al abrirse, para que el lector de pantalla lo
    // anuncie y para que Escape cierre lo que está abierto y no otra cosa.
    effect(() => {
      if (!this.open()) return;
      queueMicrotask(() => this.host.nativeElement.querySelector<HTMLElement>('.notice-modal')?.focus());
    });
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    if (this.open()) this.closed.emit();
  }
}
