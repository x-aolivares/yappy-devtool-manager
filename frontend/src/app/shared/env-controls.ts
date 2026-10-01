import { Component, computed, input, model } from '@angular/core';
import { EnvironmentInfo } from '../api-gen/models';
import { EnvPickerComponent } from './env-picker';

/** Una opción de un grupo de servicios (SSM, Secrets Manager, ...). */
export interface ServiceOption {
  readonly value: string;
  readonly label: string;
}

/** Servicios de parámetros, para las páginas que no tienen un modo propio. */
export const PARAM_SERVICES: readonly ServiceOption[] = [
  { value: 'ssm', label: 'SSM Parameter Store' },
  { value: 'secretsmanager', label: 'Secrets Manager' },
];

/**
 * Grupos de píldoras para elegir ambientes y servicios.
 *
 * Un solo lugar donde se decide cómo se ve esa sección, tomado del diseño de
 * "Leer parámetros": los grupos van en una grilla, cada uno con su etiqueta en
 * mayúsculas y sus opciones como píldoras conmutables. Todo lo que hoy repite
 * `<label>Ambientes</label>` + `<app-env-picker>` (y, en las páginas de
 * parámetros, un `<select>` de servicio) usa este componente.
 *
 * Los ambientes se manejan como un **array en orden de selección**: es lo que
 * permite derivar origen/destino sin estado duplicado (ver `region-controls`).
 * `max` acota la cantidad; `roleLabels` rotula cada slot.
 */
@Component({
  selector: 'app-env-controls',
  imports: [EnvPickerComponent],
  template: `
    <div class="pill-controls">
      @if (services().length) {
        <div class="pill-group">
          <label class="pill-label">{{ serviceLabel() }}</label>
          <div class="env-list" role="group" [attr.aria-label]="serviceLabel()">
            @for (s of services(); track s.value) {
              <button
                type="button"
                class="chip"
                [class.selected]="service() === s.value"
                [attr.aria-pressed]="service() === s.value"
                (click)="service.set(s.value)"
              >
                <span aria-hidden="true">{{ service() === s.value ? '✓' : '' }}</span>
                {{ s.label }}
              </button>
            }
          </div>
        </div>
      }

      <div class="pill-group">
        <label class="pill-label">{{ envLabel() }}</label>
        <app-env-picker
          [environments]="environments()"
          [selected]="envs()"
          [max]="max()"
          [roleLabels]="roleLabels()"
          [label]="envLabel() + ' a seleccionar'"
          (selectedChange)="envs.set($event)"
        />
      </div>
    </div>

    @if (hint(); as text) {
      <p class="muted" style="margin-top: 0.25rem; font-size: 0.75rem;">{{ text }}</p>
    }
  `,
})
export class EnvControlsComponent {
  environments = input<EnvironmentInfo[] | null>(null);

  /** Opciones del grupo de servicios. Vacío = el grupo no se dibuja. */
  services = input<readonly ServiceOption[]>([]);
  serviceLabel = input('Servicios');

  envLabel = input('Ambientes');
  /** Etiqueta bajo los grupos; va fuera del componente porque es texto de la página. */
  hint = input<string | null>(null);

  /** 0 = sin límite, 1 = un ambiente, 2 = origen + destino. */
  max = input(0);
  roleLabels = input<readonly string[]>([]);

  envs = model<string[]>([]);
  service = model<string>('');

  /** El ambiente elegido cuando `max` es 1; la página lo lee para llamar al backend. */
  readonly single = computed(() => (this.envs().length === 1 ? this.envs()[0] : ''));
}