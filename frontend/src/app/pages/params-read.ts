import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  CreateMultiParamsRequest,
  EnvironmentInfo,
  ParamsReadResponse,
  ReadEntryResultInfo,
} from '../api-gen/models';
import { EnvironmentService } from '../core/services/environment.service';
import { ParamsService } from '../core/services/params.service';
import { SessionService } from '../core/services/session.service';
import { toApiError } from '../core/services/api-error';
import { formatValue } from '../core/format';
import { EnvPickerComponent } from '../shared/env-picker';
import { StatusBadge } from '../shared/status-badge';

interface EnvPanel {
  env: string;
  region?: string | null;
  profile?: string | null;
  /** Contenido tal como está el cuadro de edición. */
  value: string;
  valueType: string;
  ok: boolean;
  error: string | null;
  differs: boolean;
}

/**
 * Leer / actualizar un parámetro o secreto en N ambientes.
 *
 * La selección sale del env-picker (nada hardcodeado); del primer ambiente se
 * muestra el valor como caja editable y `Actualizar` escribe SÓLO ese ambiente
 * vía params/multi con `envs: [env]`. Con dos o más ambientes marcados se arma
 * la sesión de trabajo (origen = primero, destino = segundo).
 */
@Component({
  selector: 'app-params-read-page',
  imports: [EnvPickerComponent, StatusBadge, RouterLink],
  template: `
    <h1>Leer Parámetros / Secretos</h1>
    <p class="muted">
      Marcá los ambientes que te interesa comparar, ingresá el nombre del parámetro o secreto y
      leé su valor en cada uno (se detecta si es secreto con el servicio elegido). Cada panel se
      ajusta a su contenido y te deja actualizar el valor sólo de ese ambiente.
    </p>

    <div class="panel">
      <div class="read-controls">
        <div class="pill-group">
          <label class="pill-label">Servicios</label>
          <div class="env-list chips" role="group" aria-label="Servicio">
            @for (s of services; track s.value) {
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
        <div class="pill-group">
          <label class="pill-label">Ambientes</label>
          <app-env-picker
            [environments]="environments()"
            [(selected)]="envs"
            variant="chips"
            label="Ambientes a leer"
          />
        </div>
      </div>

      <p class="muted" style="margin-top: 0.25rem; font-size: 0.75rem;">
        Se leen las mismas claves en todos los ambientes marcados. Con dos o más también se arma la
        sesión de trabajo: el primero es el origen y el segundo el destino.
      </p>

      @if (requireAlias()) {
        <label for="session-alias">Alias o nombre de la iniciativa</label>
        <input
          id="session-alias"
          type="text"
          [value]="sessionAlias()"
          (input)="sessionAlias.set($any($event.target).value)"
          placeholder="release/REP-325073"
          style="max-width: 26rem;"
        />
      }

      <div class="read-actions">
        <div style="flex: 1;">
          <label for="param-name">Parámetro o secreto</label>
          <input
            id="param-name"
            type="text"
            [value]="name()"
            (input)="name.set($any($event.target).value)"
            (keydown.enter)="search()"
            placeholder="/prod/ecommerce/db/master_url"
            spellcheck="false"
          />
        </div>
        <button type="button" [disabled]="busy()" (click)="search()">Buscar</button>
      </div>
    </div>

    @if (error()) {
      <div class="error-box">{{ error() }}</div>
    }

    @if (busy()) {
      <div class="panel"><span class="spinner"></span>Buscando…</div>
    }

    @if (sessionCreated()) {
      <div class="ok-box">
        Sesión de trabajo <strong>{{ sessionCreated()!.title }}</strong> lista ·
        <a [routerLink]="['/sessions', sessionCreated()!.id]">Abrir en Sesiones →</a>
      </div>
    }

    @for (panel of panels(); track panel.env) {
      <div class="panel env-value-panel">
        <div class="section-title">
          <strong>{{ panel.env }}</strong>
          @if (panel.region || panel.profile) {
            <span class="muted">{{ panel.region || '—' }} · {{ panel.profile || '—' }}</span>
          }
          @if (panel.differs) {
            <app-badge status="different" label="Valores distintos" />
          }
        </div>
        <div class="value-row">
          <textarea
            class="value-box"
            [class.value-error]="!panel.ok"
            [rows]="rowsFor(panel.value)"
            [value]="panel.value"
            (input)="onEdit(panel, $any($event.target).value)"
            spellcheck="false"
          ></textarea>
          <button
            type="button"
            class="update-btn"
            [disabled]="writingEnv() !== null"
            (click)="update(panel)"
          >Actualizar</button>
        </div>
        @if (writingEnv() === panel.env) {
          <p class="muted" style="margin-top: 0.375rem; font-size: 0.75rem;">
            <span class="spinner"></span> Escribiendo en {{ panel.env }}…
          </p>
        } @else if (writeStatus()[panel.env]) {
          <p
            class="muted"
            style="margin-top: 0.375rem; font-size: 0.75rem;"
            [style.color]="writeStatus()[panel.env]!.ok ? 'var(--ok)' : 'var(--err)'"
          >{{ writeStatus()[panel.env]!.message }}</p>
        }
      </div>
    }
  `,
})
export class ParamsReadPage {
  private readonly envService = inject(EnvironmentService);
  private readonly paramsService = inject(ParamsService);
  private readonly sessionService = inject(SessionService);

  readonly environments = signal<EnvironmentInfo[] | null>(null);
  readonly envs = signal<string[]>([]);
  readonly service = signal('ssm');
  readonly name = signal('');
  readonly sessionAlias = signal('');
  readonly requireAlias = signal(false);

  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly panels = signal<EnvPanel[]>([]);
  readonly sessionCreated = signal<{ title: string; id: string } | null>(null);
  readonly writingEnv = signal<string | null>(null);
  readonly writeStatus = signal<Record<string, { ok: boolean; message: string }>>({});

  readonly services = [
    { value: 'ssm', label: 'SSM' },
    { value: 'secretsmanager', label: 'Secrets Manager' },
  ];

  constructor() {
    const q = new URLSearchParams(location.search);
    const sessionAlias = q.get('alias') ?? q.get('session_alias') ?? '';
    const fromSession = q.get('from_session') === '1' || q.get('session_id') !== null || q.get('session') !== null;
    this.requireAlias.set(fromSession);
    if (sessionAlias) {
      this.sessionAlias.set(sessionAlias);
    }

    this.envService.list().then(
      (envs) => this.environments.set(envs.environments),
      (err) => this.error.set('No se pudieron cargar los ambientes: ' + toApiError(err).message),
    );
  }

  search() {
    const name = this.name().trim();
    if (!name) {
      this.error.set('Ingresá el nombre del parámetro o secreto.');
      return;
    }
    const envs = this.envs();
    if (!envs.length) {
      this.error.set('Seleccioná al menos un ambiente.');
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.sessionCreated.set(null);

    this.paramsService.read(envs, [name]).then(
      (d) => {
        this.busy.set(false);
        this.buildPanels(d, envs);
        this.ensureSession(name);
      },
      (err) => {
        this.busy.set(false);
        this.error.set(toApiError(err).message);
      },
    );
  }

  private buildPanels(res: ParamsReadResponse, envs: string[]): void {
    const byEnv = new Map<string, ReadEntryResultInfo[]>();
    for (const r of res.results) {
      const env = r.env ?? '';
      if (!env) continue;
      if (!byEnv.has(env)) byEnv.set(env, []);
      byEnv.get(env)!.push(r);
    }

    const reference = byEnv.get(envs[0]);
    const referenceValues = new Map(
      (reference ?? []).map((r) => [r.key, r.ok ? (r.value ?? '').trim() : null]),
    );

    this.panels.set(
      envs
        .filter((env) => byEnv.has(env))
        .map((env) => {
          const r = byEnv.get(env)![0];
          const rawValue = r.ok ? formatValue(r.value) : (r.error || 'No existe el parámetro.');
          const meta = (this.environments() ?? []).find((e) => e.env === env);
          return {
            env,
            region: meta?.region,
            profile: meta?.profile,
            value: rawValue,
            valueType: r.value_type ?? 'String',
            ok: r.ok,
            error: r.error ?? null,
            differs:
              r.ok && (referenceValues.get(r.key) ?? null) !== (r.value ?? '').trim(),
          };
        }),
    );
    this.writeStatus.set({});
  }

  rowsFor(value: string): number {
    return Math.max(2, Math.min(24, value.split(/\r?\n/).length + 1));
  }

  onEdit(panel: EnvPanel, text: string): void {
    panel.value = text;
    this.panels.update((list) => [...list]);
  }

  /** Escribe el valor editado en UN ambiente: params/multi con `envs: [env]`. */
  update(panel: EnvPanel): void {
    const name = this.name().trim();
    const value = panel.value;
    if (!value.trim()) {
      this.writeStatus.update((s) => ({
        ...s,
        [panel.env]: { ok: false, message: 'No podés guardar un valor vacío; borrá el parámetro desde Crear.' },
      }));
      return;
    }
    const text = `¿Actualizar '${name}' en ${panel.env}?\nSe escribe el valor del cuadro, sobrescribe el actual.`;
    if (!confirm(text)) {
      return;
    }

    const multi: CreateMultiParamsRequest = panel.valueType === 'SecureString'
      ? {
          name,
          value: name,
          value_type: 'SecureString',
          service: 'secretsmanager',
          secret_name: name,
          secret_value: value,
          create_secret: true,
          envs: [panel.env],
          dry_run: false,
          confirm: true,
        }
      : {
          name,
          value,
          value_type: panel.valueType || 'String',
          service: 'ssm',
          envs: [panel.env],
          dry_run: false,
          confirm: true,
        };

    this.writingEnv.set(panel.env);
    this.writeStatus.update((s) => ({ ...s, [panel.env]: undefined! }));
    this.paramsService.multi(multi).then(
        (res) => {
          this.writingEnv.set(null);
          const outcome = res.results.find((r) => r.env === panel.env);
          this.writeStatus.update((s) => ({
            ...s,
            [panel.env]: outcome?.ok
              ? { ok: true, message: outcome.message || 'Actualizado.' }
              : { ok: false, message: outcome?.error || 'Falló la escritura.' },
          }));
        },
        (err) => {
          this.writingEnv.set(null);
          this.writeStatus.update((s) => ({
            ...s,
            [panel.env]: { ok: false, message: toApiError(err).message },
          }));
        },
      );
  }

  private ensureSession(name: string) {
    const [origin, destination] = this.envs();
    if (!destination || destination === origin) return;
    const alias = this.sessionAlias().trim();
    const payload = {
      env_a: destination,
      env_b: origin,
      service: this.service() || 'ssm',
      keys: [name],
      alias,
      title: alias,
      reuse: true,
    };
    this.sessionService
      .create(payload)
      .then(
        (body) => {
          // No se navega: la vista de valores es el resultado de esta pantalla.
          // El link "Abrir en Sesiones" queda en el ok-box de arriba.
          this.sessionCreated.set({ title: body.title, id: body.id });
        },
        () => null,
      );
  }

  protected readonly formatValue = formatValue;
}
