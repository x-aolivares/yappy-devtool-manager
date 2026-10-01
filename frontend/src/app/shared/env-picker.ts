import { Component, computed, input, model } from '@angular/core';
import { EnvironmentInfo } from '../api-gen/models';

/**
 * Selector de ambientes como paneles apilados.
 *
 * Reemplaza a los `<select>` y a los checkboxes: cada ambiente es un botón
 * conmutable con `aria-pressed`, y el `✓` sólo aparece en los seleccionados.
 * La lista sigue viniendo de `EnvironmentService.list()` (los `.env` de
 * `config/`), así que no hay nada hardcodeado acá.
 *
 * La selección se emite como array **en orden de selección**, que es lo que
 * permite que las páginas de diff deriven origen/destino sin cambiar de estado.
 */
@Component({
  selector: 'app-env-picker',
  template: `
    <div class="env-list" role="group" [attr.aria-label]="label()">
      @for (e of environments() ?? []; track e.env) {
        <button
          type="button"
          class="env-item"
          [class.selected]="isSelected(e.env)"
          [disabled]="!environments() || (atLimit() && !isSelected(e.env))"
          [attr.aria-pressed]="isSelected(e.env)"
          (click)="toggle(e.env)"
        >
          <span class="env-check" aria-hidden="true">{{ isSelected(e.env) ? '✓' : '' }}</span>
          <span class="env-text">
            <span class="env-name">{{ e.env }}</span>
            <span class="env-meta">{{ e.region || '—' }} · {{ e.profile || '—' }}</span>
            @if (e.load_error) {
              <span class="env-error">{{ e.load_error }}</span>
            }
          </span>
          @if (roleOf(e.env); as role) {
            <span class="env-role">{{ role }}</span>
          }
        </button>
      } @empty {
        <p class="muted">No se encontraron ambientes en config/.</p>
      }
    </div>
  `,
})
export class EnvPickerComponent {
  environments = input<EnvironmentInfo[] | null>(null);
  /** 0 = sin límite. 1 = un solo ambiente. 2 = origen + destino. */
  max = input(0);
  label = input('Ambientes');
  /** Etiqueta del slot según el orden de selección (ej. `['Origen', 'Destino']`). */
  roleLabels = input<readonly string[]>([]);

  selected = model<string[]>([]);

  protected readonly atLimit = computed(
    () => this.max() > 0 && this.selected().length >= this.max(),
  );

  protected isSelected(env: string): boolean {
    return this.selected().includes(env);
  }

  /** Nombre del slot que ocupa este ambiente, si el modo lo define. */
  protected roleOf(env: string): string | null {
    return this.roleLabels()[this.selected().indexOf(env)] ?? null;
  }

  protected toggle(env: string): void {
    const current = this.selected();
    if (current.includes(env)) {
      this.selected.set(current.filter((e) => e !== env));
      return;
    }
    if (this.atLimit()) {
      return;
    }
    this.selected.set([...current, env]);
  }
}
