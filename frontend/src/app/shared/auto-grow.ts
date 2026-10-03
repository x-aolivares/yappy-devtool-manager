/**
 * Hace que un `<textarea>` crezca con su contenido en vez de tener alto fijo.
 *
 * El wireframe de referencia pide que el editor de SQL se expanda "completamente
 * al tamaño del contenido". CSS tiene `field-sizing: content` para eso, pero
 * Firefox todavía no lo soporta, y ahí el textarea volvería a su alto fijo sin
 * aviso. Una directiva cuesta unas líneas más y funciona en todos.
 *
 * El alto final lo decide el CSS, no esta clase: se escribe el `scrollHeight`
 * completo y, si el `max-height` de la hoja lo recortan, el navegador recorta y
 * aparece el scroll. Por eso no hay lógica de tope acá — y por eso el tope se
 * cambia en CSS, con cualquier unidad, sin volver a compilar.
 *
 * Se redimensiona por dos caminos distintos y ambos hacen falta:
 *
 * - `input`, que es el tecleo y el pegado;
 * - `autoGrowValue`, que es el valor que Angular pone por `[value]`. Generar un
 *   script NO dispara `input`, así que sin este input el editor se quedaría con
 *   el alto anterior después de Generar, que es justo cuando más se necesita.
 */
import {
  afterNextRender,
  Directive,
  ElementRef,
  HostListener,
  effect,
  inject,
  input,
} from '@angular/core';

@Directive({ selector: 'textarea[appAutoGrow]' })
export class AutoGrowDirective {
  /** Valor que el host le pone al textarea, para seguirlo sin esperar un `input`. */
  readonly autoGrowValue = input<string | null>(null);

  private readonly host = inject<ElementRef<HTMLTextAreaElement>>(ElementRef);

  constructor() {
    // El primer ajuste necesita layout listo: antes de que se resuelvan las
    // fuentes, `scrollHeight` mide otra cosa y el alto queda corto.
    afterNextRender(() => this.resize());

    effect(() => {
      this.autoGrowValue();
      this.resize();
    });
  }

  @HostListener('input')
  onInput(): void {
    this.resize();
  }

  /** Colapsar a `auto` antes de medir es lo que hace que también encoja. */
  private resize(): void {
    const el = this.host.nativeElement;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }
}
