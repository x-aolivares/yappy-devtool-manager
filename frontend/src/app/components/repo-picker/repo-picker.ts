import { Component, ElementRef, computed, input, model, output, signal, viewChild } from '@angular/core';

/**
 * El campo de repositorio con sus sugerencias: un combobox con dropdown propio.
 *
 * No es un `<datalist>`: ese popup lo dibuja el navegador, no se puede estilar
 * y se ve distinto en cada SO. Acá se caía encima del panel y cortado contra el
 * sidebar. Este es un `role="listbox"` anclado al campo, con los tokens del
 * sistema visual y el mismo comportamiento que el buscador de Bitbucket: se abre
 * al enfocar, filtra mientras se escribe y se elige con el teclado o el mouse.
 *
 * El filtrado vive acá y no en la pantalla que lo usa: la que lo llama pasa la
 * lista completa y no tiene que reimplementar la búsqueda. Coincide por
 * substring —`refund` encuentra `yappy-…-transaction-refund`— y no sólo por
 * prefijo, que es justamente lo que no encontraría.
 */
@Component({
  selector: 'app-repo-picker',
  templateUrl: './repo-picker.html',
  styleUrl: './repo-picker.scss',
})
export class RepoPicker {
  /** El texto del campo, que va y viene con la pantalla que lo usa. */
  value = model('');
  /** Todos los repos del workspace, ya ordenados por el backend. */
  options = input.required<readonly string[]>();
  placeholder = input('');
  /** El `id` del input, que es el que apunta el `<label>` de la pantalla. */
  controlId = input('repo');

  /** Enter sin fila marcada: "Consultar". Con fila marcada se elige la fila. */
  submit = output<void>();

  /** Tope de filas. Un workspace de 800 repos no es un dropdown navegable. */
  private static readonly MAX_OPTIONS = 50;

  readonly open = signal(false);
  /** Fila resaltada, `-1` mientras no se haya navegado con el teclado. */
  readonly activeIndex = signal(-1);

  readonly matches = computed(() => {
    const needle = this.value().trim().toLowerCase();
    const all = this.options();
    const found = needle ? all.filter((name) => name.toLowerCase().includes(needle)) : all;
    return found.slice(0, RepoPicker.MAX_OPTIONS);
  });

  /** La línea de arriba del dropdown: cuánto hay y contra qué se está filtrando. */
  readonly header = computed(() => {
    const needle = this.value().trim();
    return needle
      ? `Coinciden con «${needle}» (${this.matches().length})`
      : `Repositorios (${this.options().length})`;
  });

  get listId(): string {
    return `${this.controlId()}-list`;
  }

  get statusId(): string {
    return `${this.controlId()}-status`;
  }

  readonly activeId = computed(() => {
    const index = this.activeIndex();
    return this.open() && index >= 0 ? `${this.listId}-${index}` : null;
  });

  /** El `<ul>` del dropdown, que sólo existe mientras está abierto. */
  private readonly list = viewChild<ElementRef<HTMLUListElement>>('list');

  onInput(raw: string): void {
    this.value.set(raw);
    // Escribir borra la fila marcada: `Enter` tiene que seguir significando
    // "consultar", no "elegir lo que quedó resaltado de antes".
    this.activeIndex.set(-1);
    this.open.set(true);
  }

  onKeydown(event: KeyboardEvent): void {
    switch (event.key) {
      case 'ArrowDown':
      case 'ArrowUp': {
        event.preventDefault();
        if (!this.open()) {
          this.open.set(true);
          this.activeIndex.set(this.matches().length ? 0 : -1);
          return;
        }
        this.move(event.key === 'ArrowDown' ? 1 : -1);
        return;
      }
      case 'Enter': {
        const name = this.matches()[this.activeIndex()];
        if (this.open() && name) {
          event.preventDefault();
          this.pick(name);
          return;
        }
        this.open.set(false);
        this.submit.emit();
        return;
      }
      case 'Escape':
        // Con `stopPropagation` no se lo come un modal que esté abajo: Escape
        // cierra el dropdown, y recién si no hay dropdown cierra lo demás.
        if (this.open()) {
          event.preventDefault();
          event.stopPropagation();
          this.open.set(false);
        }
        return;
      case 'Tab':
        this.open.set(false);
        return;
    }
  }

  pick(name: string): void {
    this.value.set(name);
    this.open.set(false);
    this.activeIndex.set(-1);
    // El foco se queda en el campo: se eligió un repo y todavía falta apretar
    // "Consultar", o escribir otra cosa para cambiar de idea.
  }

  /** El click en una fila no puede sacarle el foco al input: ver el template. */
  onOptionMousedown(event: Event): void {
    event.preventDefault();
  }

  private move(step: number): void {
    const count = this.matches().length;
    if (!count) {
      this.activeIndex.set(-1);
      return;
    }
    const current = this.activeIndex();
    // Sin envolver: en los extremos se queda, que es lo que hace el navegador y
    // evita que un ArrowDown de más salte al otro extremo de la lista de golpe.
    const next =
      current < 0
        ? step > 0
          ? 0
          : count - 1
        : Math.min(count - 1, Math.max(0, current + step));
    this.activeIndex.set(next);
    this.scrollToActive();
  }

  private scrollToActive(): void {
    const list = this.list();
    if (!list) return;
    const active = list.nativeElement.querySelector('.is-active') as HTMLElement | null;
    // `scrollIntoView` no existe en el entorno de tests, y la fila visible no
    // depende de que se llame: sólo del scroll del teclado.
    if (active && typeof active.scrollIntoView === 'function') {
      active.scrollIntoView({ block: 'nearest' });
    }
  }
}
