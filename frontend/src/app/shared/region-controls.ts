import { Component, computed, input, model } from '@angular/core';
import { EnvironmentInfo } from '../api-gen/models';
import { EnvControls, PARAM_SERVICES } from './env-controls';

/**
 * Controles de un par origen → destino, sobre los grupos de píldoras.
 *
 * `envB` (origen) y `envA` (destino) siguen siendo la única fuente de verdad:
 * el picker los refleja en orden de selección y al hacer click se escribe de
 * vuelta. Así los `set` programáticos desde las páginas (params-diff los lee de
 * la URL) siguen reflejándose sin duplicar estado.
 *
 * Las páginas que eligen N ambientes o uno solo usan `app-env-controls` directo.
 */
@Component({
  selector: 'app-region-controls',
  imports: [EnvControls],
  template: `
    <app-env-controls
      [environments]="environments()"
      [services]="withService() ? services() : []"
      [roleLabels]="roles"
      [envLabel]="envLabel()"
      [hint]="hint()"
      [max]="2"
      [envs]="pair()"
      (envsChange)="onPair($event)"
      [(service)]="service"
    />

    @if (withName()) {
      <div class="form-grid">
        <div>
          <label for="name">Nombre del parámetro / secreto</label>
          <input
            id="name"
            type="text"
            [value]="nameValue()"
            (input)="nameValue.set($any($event.target).value)"
            placeholder="p. ej. /yappy/dev/rate"
            spellcheck="false"
          />
        </div>
      </div>
    }
  `,
})
export class RegionControls {
  environments = input<EnvironmentInfo[] | null>(null);
  withService = input(true);
  withName = input(false);

  /** Opciones del grupo de servicios; por defecto los de parámetros. */
  services = input<readonly { value: string; label: string }[]>(PARAM_SERVICES);
  envLabel = input('Ambientes');
  hint = input<string | null>(null);

  envB = model(''); // origen
  envA = model(''); // destino
  service = model<string>('');
  nameValue = model('');

  protected readonly roles = ['Origen', 'Destino'];

  /** Los dos ambientes en orden de selección: primero origen, segundo destino. */
  protected readonly pair = computed(() => {
    const out: string[] = [];
    if (this.envB()) out.push(this.envB());
    if (this.envA() && this.envA() !== this.envB()) out.push(this.envA());
    return out;
  });

  protected onPair(sel: string[]): void {
    this.envB.set(sel[0] ?? '');
    this.envA.set(sel[1] ?? '');
  }
}