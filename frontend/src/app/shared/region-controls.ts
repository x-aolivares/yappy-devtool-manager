import { Component, computed, input, model } from '@angular/core';
import { EnvironmentInfo } from '../api-gen/models';
import { EnvPickerComponent } from './env-picker';

@Component({
  selector: 'app-region-controls',
  imports: [EnvPickerComponent],
  template: `
    <div style="margin-bottom: 1.125rem;">
      <label id="envs-label">Ambientes</label>
      <app-env-picker
        [environments]="environments()"
        [selected]="selected()"
        [max]="2"
        [roleLabels]="roles"
        label="Ambientes de origen y destino"
        (selectedChange)="onSelection($event)"
      />
      <p class="muted" style="margin-top: 0.25rem; font-size: 0.75rem;">
        Elegí dos: el primero es el de origen y el segundo el de destino.
      </p>
    </div>
    <div class="form-grid">
      @if (withService()) {
        <div>
          <label for="service">Servicio</label>
          <select
            id="service"
            [value]="service() || ''"
            (change)="service.set($any($event.target).value)">
            <option value="" disabled [selected]="!service()">Seleccione un servicio</option>
            <option value="ssm">SSM Parameter Store</option>
            <option value="secretsmanager">Secrets Manager</option>
          </select>
        </div>
      }
      @if (withName()) {
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
      }
    </div>
  `,
})
export class RegionControlsComponent {
  environments = input<EnvironmentInfo[] | null>(null);
  withService = input(true);
  withName = input(false);

  envB = model('');
  envA = model('');
  service = model<string>('');
  nameValue = model('');

  protected readonly roles = ['Origen', 'Destino'];

  /**
   * `envB` (origen) y `envA` (destino) siguen siendo la única fuente de verdad:
   * el panel los refleja en orden, y al hacer click se escribe de vuelta. Así
   * los `set` programáticos desde las páginas (params-diff los lee de la URL)
   * siguen reflejándose sin duplicar estado.
   */
  protected readonly selected = computed(() => {
    const out: string[] = [];
    if (this.envB()) {
      out.push(this.envB());
    }
    if (this.envA() && this.envA() !== this.envB()) {
      out.push(this.envA());
    }
    return out;
  });

  protected onSelection(sel: string[]): void {
    this.envB.set(sel[0] ?? '');
    this.envA.set(sel[1] ?? '');
  }
}
