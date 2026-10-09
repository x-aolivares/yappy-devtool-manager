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
 * Tiene dos modos, y el segundo existe porque `confirm()` nativo no los tenía:
 * `aviso` (el default) es un botón que cierra y se va; `default` y `danger` son
 * una confirmación con **dos** salidas, cancelar y confirmar. El `confirm()` del
 * navegador no decía qué iba a pasar, y su única forma de salir sin confirmar
 * era el mismo botón de confirmar.
 *
 * Que el modal sea asíncrono es la consecuencia de diseño: la respuesta del
 * usuario llega por `output`, no por el retorno de un método. Por eso la página
 * que lo abre tiene que partir su acción en dos —abrir, y ejecutar cuando
 * confirme— en vez de seguir el código linealmente. Ver `pages/sessions.ts`.
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
        [class.notice-modal--danger]="tone() === 'danger'"
        role="dialog"
        aria-modal="true"
        [attr.aria-labelledby]="titleId"
        tabindex="-1"
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
          <!-- Las clases notice-modal__confirm y notice-modal__cancel no llevan
               estilo: son el gancho para que los tests lleguen al botón correcto
               sin depender del rótulo, que puede cambiar con el copy. En modo
               aviso sólo hay un botón, como antes. -->
          @if (tone() === 'aviso') {
            <button type="button" class="notice-modal__confirm" (click)="closed.emit()">
              {{ confirmLabel() }}
            </button>
          } @else {
            <!-- Cancelar va a la izquierda y el de confirmar a la derecha: en un
                 diálogo, la acción por defecto de la interfaz es la segura. -->
            <button
              type="button"
              class="secondary notice-modal__cancel"
              (click)="closed.emit()"
            >
              Cancelar
            </button>
            <button
              type="button"
              class="notice-modal__confirm"
              [class.notice-modal__confirm--danger]="tone() === 'danger'"
              (click)="confirmed.emit()"
            >
              {{ confirmLabel() }}
            </button>
          }
        </div>
      </div>
    }
  `,
})
export class NoticeModal {
  readonly open = input(false);
  readonly title = input.required<string>();
  readonly confirmLabel = input('Entendido');
  /**
   * `aviso` = un botón que se cierra. `default` / `danger` = confirmación con
   * cancelar. El default es `aviso` para que los usos que sólo muestran no
   * tengan que declarar nada.
   */
  readonly tone = input<'aviso' | 'default' | 'danger'>('aviso');
  /** El usuario confirmó. Deliberadamente no se llama `destructive`: tres de los
   *  casos que lo usan —ejecutar un script, migrar datos, actualizar un
   *  parámetro— no son destructivos. `danger` es el tono, no el nombre de la
   *  acción. */
  readonly confirmed = output<void>();
  /** Se cerró sin confirmar: ✕, Escape, backdrop o Cancelar. */
  readonly closed = output<void>();

  /** Único por instancia: dos modales a la vez no deben compartir `aria-labelledby`. */
  protected readonly titleId = `notice-modal-${++seq}`;

  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  constructor() {
    // El diálogo recibe el foco al abrirse, para que el lector de pantalla lo
    // anuncie y para que Escape cierre lo que está abierto y no otra cosa.
    // `tabindex="-1"` es lo que hace que eso funcione: sin él, `focus()` sobre
    // un `div` es un no-op silencioso y el foco se queda en la página de atrás.
    effect(() => {
      if (!this.open()) return;
      queueMicrotask(() => this.host.nativeElement.querySelector<HTMLElement>('.notice-modal')?.focus());
    });
  }

  /** Escape siempre cancela, nunca confirma. Es lo seguro: un Escape de más no
   *  puede disparar un borrado, y un borrado es lo único que no se arregla con
   *  otro clic. */
  @HostListener('document:keydown.escape')
  onEscape(): void {
    if (this.open()) this.closed.emit();
  }
}
