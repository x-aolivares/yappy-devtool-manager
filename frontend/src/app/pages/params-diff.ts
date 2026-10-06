import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { EnvironmentInfo } from '../api-gen/models';
import { EnvironmentService } from '../core/services/environment.service';
import { ParamsService } from '../core/services/params.service';
import { SessionService } from '../core/services/session.service';
import { toApiError } from '../core/services/api-error';
import { awsEnvironments, formatValue } from '../core/format';
import { serializeMerged } from '../core/params-merge';
import type { ChangeRow } from '../core/params-merge';
import { ParamsDiffResponse, UpdateSessionItemRequest } from '../api-gen/models';
import { RegionControlsComponent } from '../shared/region-controls';
import { StatusBadge } from '../shared/status-badge';
import { CopyButton } from '../shared/copy-button';
import { PaginationBarComponent } from '../shared/pagination-bar';
import { paginate } from '../shared/paginate';

interface DiffChange {
  path?: string;
  op?: string;
  old?: unknown;
  new?: unknown;
  [key: string]: unknown;
}

interface PairField {
  text: string;
  include: boolean;
}

interface PairState {
  param: PairField;
  secret: PairField;
}

type Mode =
  | 'idle'
  | 'pair'
  | 'equal'
  | 'json'
  | 'plain'
  | 'script'
  | 'create'
  | 'none';

type PairKey = 'param' | 'secret';

@Component({
  selector: 'app-params-diff-page',
  imports: [RouterLink, RegionControlsComponent, StatusBadge, CopyButton, PaginationBarComponent],
  template: `
    <h1>Diff de Parámetros / Secretos</h1>
    <p class="muted">
      Compara un parámetro entre la región de <strong>origen</strong> y la de <strong>destino</strong>.
      Si el valor es JSON solo se actualizan las claves que cambiaron; el resto del parámetro queda intacto.
      Los valores que solo existen en destino se eliminan únicamente si marcás la opción.
    </p>
    @if (sessionId()) {
      <a class="muted" [routerLink]="['/sessions', sessionId()]">← Volver a la sesión</a>
    }

    <div class="panel">
      <app-region-controls
        [environments]="awsEnvironments()"
        [withName]="true"
        hint="Elegí dos: el primero es el de origen y el segundo el de destino."
        [(envB)]="envB"
        [(envA)]="envA"
        [(service)]="service"
        [(nameValue)]="name"
      />
      <div class="actions" style="justify-content:flex-end;">
        @if (service() === 'ssm') {
          <label class="chk">
            <input
              type="checkbox"
              [checked]="withSecret()"
              (change)="withSecret.set($any($event.target).checked)"
            />
            Este parámetro almacena un secreto
            <span class="muted" style="font-size:0.75rem;">(SSM + secreto emparejado en Secrets Manager)</span>
          </label>
        }
        <label class="chk">
          <input
            type="checkbox"
            [checked]="includeDeletes()"
            (change)="includeDeletes.set($any($event.target).checked)"
          />
          Incluir eliminaciones
          <span class="muted" style="font-size:0.75rem;">(por lo general no se eliminan)</span>
        </label>
        <button type="button" [disabled]="busy()" (click)="compare()">Comparar</button>
      </div>
    </div>

    @if (error()) {
      <div class="error-box">{{ error() }}</div>
    }

    @if (data()) {
      <div class="panel">
        <div style="display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap;">
          <app-badge [status]="data()!.status" />
          <span class="muted">{{ data()!.name }}</span>
          <span class="muted">{{ data()!.service }}</span>
          <span class="muted">{{ data()!.env_b }} → {{ data()!.env_a }}</span>
        </div>
        @for (n of data()!.notes ?? []; track n) {
          <div class="note">• {{ n }}</div>
        }
      </div>

      @switch (mode()) {
        @case ('pair') {
          @if (data()!.param_needs_write || data()!.secret_needs_write || withSecret()) {
            <div class="columns" style="margin-top:0.875rem;">
              <div class="panel">
                <div class="section-title"><strong>Región de Origen — {{ data()!.env_b }}</strong></div>
                <div class="muted" style="font-size:0.75rem;">Parámetro</div>
                <pre>{{ pre(data()!.value_b ?? '') }}</pre>
                <div class="muted" style="font-size:0.75rem; margin-top:0.5rem;">Secreto</div>
                <pre>{{ pre(data()!.secret_value_b ?? '') }}</pre>
              </div>
              <div class="panel">
                <div class="section-title"><strong>Región Destino — {{ data()!.env_a }}</strong></div>
                <div class="muted" style="font-size:0.75rem;">Parámetro</div>
                <pre>{{ pre(data()!.value_a ?? '') }}</pre>
                <div class="muted" style="font-size:0.75rem; margin-top:0.5rem;">Secreto</div>
                <pre>{{ pre(data()!.secret_value_a ?? '') }}</pre>
              </div>
            </div>

            <div class="panel">
              <div class="section-title">
                <strong>Cambios</strong>
                <span class="muted">
                  (se actualiza primero el secreto, luego el parámetro — nunca en una sola operación)
                </span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th></th>
                    <th>Recurso</th>
                    <th>Operación</th>
                    <th>Valor actual en la región destino</th>
                    <th>Valor a aplicar</th>
                  </tr>
                </thead>
                <tbody>
                  @for (row of pairPage.visible(); track row.key) {
                    <tr>
                      <td>
                        <input
                          type="checkbox"
                          class="pair-include"
                          [checked]="row.include"
                          (change)="togglePair(row.key, $any($event.target).checked)"
                        />
                      </td>
                      <td><code>{{ row.label }}</code></td>
                      <td><app-badge [status]="row.status === 'missing_in_a' ? 'missing_in_a' : 'different'" [label]="row.status === 'missing_in_a' ? 'crear' : 'actualizar'" /></td>
                      <td><pre style="margin:0; padding:0.375rem 0.5rem; font-size:0.75rem;">{{ row.current }}</pre></td>
                      <td>
                        <textarea
                          class="change-input pair-input"
                          rows="1"
                          spellcheck="false"
                          [value]="pairText(row.key)"
                          (input)="setPairText(row.key, $any($event.target).value)"
                        ></textarea>
                      </td>
                    </tr>
                  }
                </tbody>
              </table>

              <app-pagination-bar [p]="pairPage" />
            </div>
            <div class="columns" style="align-items:start; margin-top:0.875rem;">
              <div class="panel">
                <div class="section-title">
                  <strong>Valores resultantes hacia la región destino ({{ data()!.env_a }})</strong>
                </div>
                <pre class="script-block">{{ pairPreview() }}</pre>
                <div class="actions" style="margin-top:0.625rem;">
                  <app-copy-button [text]="pairPreview()" label="Copiar valor" />
                </div>
              </div>
              <div class="panel script-block">
                <div class="section-title">
                  <strong>Comandos de actualización (orden: secreto → parámetro)</strong>
                </div>
                <pre class="script-block">{{ scriptText() }}</pre>
                <div class="actions" style="margin-top:0.625rem;">
                  <app-copy-button [text]="scriptText()" label="Copiar comando" />
                  <button type="button" (click)="execPair()" [disabled]="busy()">
                    Ejecutar en {{ data()!.env_a }}
                  </button>
                </div>
                @if (execResult()) {
                  <div class="note {{ execResult()!.isError ? 'err' : 'ok' }}">
                    {{ execResult()!.text }}
                  </div>
                }
              </div>
            </div>
          }
        }
        @case ('json') {
          <div class="panel">
            <div class="section-title">
              <strong>Cambios</strong>
              <span class="muted">(marcá los que querés llevar a destino y editá los valores a aplicar)</span>
            </div>
            <table>
              <thead>
                <tr>
                  <th></th>
                  <th>Ruta</th>
                  <th>Operación</th>
                  <th>Valor actual en la región destino</th>
                  <th>Valor a aplicar</th>
                </tr>
              </thead>
              <tbody>
                @for (i of jsonPage.visibleIndices(); track i) {
                  @let row = rows()[i];
                  <tr>
                    <td>
                      <input
                        type="checkbox"
                        class="change-include"
                        [checked]="row.include"
                        (change)="toggleRow(i, $any($event.target).checked)"
                      />
                    </td>
                    <td><code>{{ row.path }}</code></td>
                    <td>
                      <app-badge
                        [status]="row.op === 'del' ? 'missing_in_b' : 'different'"
                        [label]="row.op"
                      />
                    </td>
                    <td><pre style="margin:0; padding:0.375rem 0.5rem; font-size:0.75rem;">{{ fmtValue(row.old) }}</pre></td>
                    <td>
                      @if (row.op === 'del') {
                        <span class="muted">Se elimina la clave</span>
                      } @else {
                        <textarea
                          class="change-input"
                          rows="1"
                          spellcheck="false"
                          [value]="row.text"
                          (input)="setRowText(i, $any($event.target).value)"
                        ></textarea>
                      }
                    </td>
                  </tr>
                }
              </tbody>
            </table>

            <app-pagination-bar [p]="jsonPage" />
          </div>
          <div class="columns" style="align-items:start; margin-top:0.875rem;">
            <div class="panel">
              <div class="section-title">
                <strong>Valor resultante hacia la region destino ({{ data()!.env_a }})</strong>
              </div>
              <pre class="script-block">{{ previewText() }}</pre>
              <div class="actions" style="margin-top:0.625rem;">
                <app-copy-button [text]="previewText()" label="Copiar valor" />
              </div>
            </div>
            <div class="panel script-block">
              <div class="section-title">
                <strong>Comando de actualización para la región destino ({{ data()!.env_a }})</strong>
              </div>
              <pre class="script-block">{{ scriptText() }}</pre>
              <div class="actions" style="margin-top:0.625rem;">
                <app-copy-button [text]="scriptText()" label="Copiar comando" />
                <button type="button" (click)="execUpdate(serialized())" [disabled]="busy()">
                  Ejecutar en {{ data()!.env_a }}
                </button>
              </div>
              @if (execResult()) {
                <div class="note {{ execResult()!.isError ? 'err' : 'ok' }}">
                  {{ execResult()!.text }}
                </div>
              }
            </div>
          </div>
        }
        @case ('plain') {
          <div class="panel">
            <div class="section-title">
              <strong>Valor a aplicar en la región destino ({{ data()!.env_a }})</strong>
              <span class="muted">(texto plano, no JSON)</span>
            </div>
            <textarea
              class="change-input"
              rows="4"
              spellcheck="false"
              [value]="rows()[0].text"
              (input)="setRowText(0, $any($event.target).value)"
            ></textarea>
          </div>
          <div class="columns" style="align-items:start; margin-top:0.875rem;">
            <div class="panel">
              <div class="section-title"><strong>Valor resultante hacia la region destino</strong></div>
              <pre class="script-block">{{ previewText() }}</pre>
              <div class="actions" style="margin-top:0.625rem;">
                <app-copy-button [text]="previewText()" label="Copiar valor" />
              </div>
            </div>
            <div class="panel script-block">
              <div class="section-title">
                <strong>Comando de actualización para la región destino ({{ data()!.env_a }})</strong>
              </div>
              <pre class="script-block">{{ scriptText() }}</pre>
              <div class="actions" style="margin-top:0.625rem;">
                <app-copy-button [text]="scriptText()" label="Copiar comando" />
                <button type="button" (click)="execUpdate(serialized())" [disabled]="busy()">
                  Ejecutar en {{ data()!.env_a }}
                </button>
              </div>
              @if (execResult()) {
                <div class="note {{ execResult()!.isError ? 'err' : 'ok' }}">
                  {{ execResult()!.text }}
                </div>
              }
            </div>
          </div>
        }
        @case ('script') {
          <div class="columns" style="align-items:start; margin-top:0.875rem;">
            <div class="panel script-block">
              <div class="section-title">
                <strong>Comando de actualización para la región destino ({{ data()!.env_a }})</strong>
              </div>
              <pre>{{ preScript() }}</pre>
              <div class="actions" style="margin-top:0.625rem;">
                <app-copy-button [text]="preScript()" label="Copiar script" />
                <button type="button" (click)="execUpdate(data()!.value_b ?? '')" [disabled]="busy()">
                  Ejecutar en {{ data()!.env_a }}
                </button>
              </div>
            </div>
            @if (data()!.is_json) {
              <div class="panel">
                <div class="section-title">
                  <strong>Valor a aplicar en la región destino ({{ data()!.env_a }})</strong>
                  <span class="muted">(solo claves cambiadas)</span>
                </div>
                <pre>{{ pre(data()!.patch_value ?? '') }}</pre>
              </div>
            }
          </div>
        }
        @case ('create') {
          <div class="columns" style="align-items:start; margin-top:0.875rem;">
            <div class="panel script-block">
              <div class="section-title"><strong>Data para crear — {{ createRegion() }}</strong></div>
              <pre class="script-block">{{ createValue() }}</pre>
              <div class="actions" style="margin-top:0.625rem;">
                <app-copy-button [text]="createValue()" label="Copiar comando" />
              </div>
            </div>
            <div class="panel script-block">
              <div class="section-title"><strong>Comando de creación — {{ createRegion() }}</strong></div>
              <pre class="script-block">{{ scriptText() }}</pre>
              <div class="actions" style="margin-top:0.625rem;">
                <app-copy-button [text]="scriptText()" label="Copiar comando" />
                <button type="button" (click)="execCreate()" [disabled]="busy()">
                  Crear en {{ createRegion() }}
                </button>
                @if (data()!.status === 'missing_in_b') {
                  <button type="button" class="secondary" (click)="execDelete()" [disabled]="busy()">
                    Solo eliminar en {{ data()!.env_a }}
                  </button>
                }
              </div>
              @if (execResult()) {
                <div class="note {{ execResult()!.isError ? 'err' : 'ok' }}">
                  {{ execResult()!.text }}
                </div>
              }
            </div>
          </div>
        }
      }
    }

    @if (busy() && !data()) {
      <div class="panel"><span class="spinner"></span>{{ busyText() }}</div>
    }
  `,
})
export class ParamsDiffPage {
  private readonly envService = inject(EnvironmentService);
  private readonly paramsService = inject(ParamsService);
  private readonly sessionService = inject(SessionService);

  readonly environments = signal<EnvironmentInfo[] | null>(null);
  /** `local` no es un ambiente de AWS: esta página solo ofrece los que lo son. */
  readonly awsEnvironments = computed(() => awsEnvironments(this.environments()));
  readonly envA = signal('');
  readonly envB = signal('');
  readonly service = signal<string>('');
  readonly name = signal('');
  readonly withSecret = signal(false);
  readonly includeDeletes = signal(false);

  readonly data = signal<ParamsDiffResponse | null>(null);
  readonly busy = signal(false);
  readonly busyText = signal('');
  readonly error = signal<string | null>(null);
  readonly execResult = signal<{ text: string; isError: boolean } | null>(null);
  readonly fmtValue = formatValue;

  readonly rows = signal<ChangeRow[]>([]);
  readonly previewText = signal('');
  readonly scriptText = signal('');
  readonly pairState = signal<PairState | null>(null);
  readonly pairPreview = signal('');
  readonly createTarget = signal<'a' | 'b'>('b');
  readonly createValue = signal('');

  readonly sessionId = signal<string | null>(new URLSearchParams(location.search).get('session'));

  private applyTimer: ReturnType<typeof setTimeout> | null = null;
  private applySeq = 0;

  readonly mode = computed<Mode>(() => {
    const d = this.data();
    if (!d) return 'idle';
    if (d.pair) return 'pair';
    if (d.status === 'equal') return 'equal';
    const hasChanges = Array.isArray(d.changes) && d.changes.length > 0;
    if (hasChanges && d.is_json) return 'json';
    if (hasChanges && !d.is_json) return 'plain';
    if (d.script && d.status === 'different') return 'script';
    if (d.status === 'missing_in_a' || d.status === 'missing_in_b' || d.status === 'none') {
      return 'create';
    }
    return 'none';
  });

  readonly pairRows = computed<{ key: PairKey; label: string; current: string; status: string; include: boolean }[]>(() => {
    const d = this.data();
    if (!d) return [];
    return [
      {
        key: 'secret' as PairKey,
        label: 'Secreto (Secrets Manager)',
        current: formatValue(d.secret_value_a ?? null),
        status: d.secret_status ?? '',
        include: !!(d.secret_needs_write || this.withSecret()),
      },
      {
        key: 'param' as PairKey,
        label: 'Parámetro (SSM)',
        current: formatValue(d.value_a ?? null),
        status: d.param_status ?? '',
        include: !!d.param_needs_write,
      },
    ].filter((r) =>
      r.key === 'secret'
        ? d.secret_needs_write || this.withSecret()
        : d.param_needs_write,
    );
  });

  readonly serialized = computed(() => {
    const isJSON = this.mode() === 'json';
    return serializeMerged(this.rows(), isJSON, this.data()?.value_a ?? '');
  });

  /**
   * Paginación de las dos tablas de cambios.
   *
   * `pairPage` es la tabla del caso "secreto + parámetro", que tiene a lo sumo dos
   * filas: la barra no se dibuja nunca, pero el estado existe y no cuesta nada. La
   * que importa es `jsonPage`, porque un JSON de configuración puede tener cientos
   * de claves y revisarlas todas en una sola tabla es lo que hacía la pantalla
   * inusable.
   *
   * Se pagina `rows()` —la lista completa— y no lo que se ve: el `@for` de esa
   * tabla marca por **posición absoluta**, y usar el índice de la página haría que
   * marcar la segunda fila de la página 3 desmarcara la segunda de la página 1.
   */
  readonly pairPage = paginate(() => this.pairRows());
  readonly jsonPage = paginate(() => this.rows());

  readonly createRegion = computed(() => {
    const d = this.data();
    return this.createTarget() === 'a' ? d?.env_a ?? '' : d?.env_b ?? '';
  });

  readonly createDesc = computed(() => {
    const d = this.data();
    if (!d) return '';
    if (d.status === 'none') {
      return 'No existe en ninguna región — escribí la data para crearlo.';
    }
    if (d.status === 'missing_in_a') {
      return `Falta en la región destino (${d.env_a}) — el valor de origen se puede editar antes de crear.`;
    }
    return `Falta en la región de origen (${d.env_b}) — escribí la data para crearlo.`;
  });

  constructor() {
    this.envService.list().then(
      (envs) => {
        this.environments.set(envs.environments);
        this.applyQueryParams();
      },
      (err) => this.error.set('No se pudieron cargar los ambientes: ' + toApiError(err).message),
    );
  }

  private applyQueryParams() {
    const q = new URLSearchParams(location.search);
    const name = q.get('name');
    const envAq = q.get('env_a');
    const envBq = q.get('env_b');
    const service = q.get('service');
    const withSecret = q.get('with_secret');

    if (name != null) this.name.set(name);
    if (envAq) this.envA.set(envAq);
    if (envBq) this.envB.set(envBq);
    if (service === 'ssm' || service === 'secretsmanager') this.service.set(service);
    if (withSecret && withSecret !== '0' && this.service() === 'ssm') {
      this.withSecret.set(true);
    }

    if (name != null && name.trim()) {
      void this.compare();
    }
  }

  compare() {
    const envA = this.envA();
    const envB = this.envB();
    const name = this.name().trim();
    if (!envA || !envB) {
      this.error.set('Seleccioná las dos regiones.');
      return;
    }
    if (!name) {
      this.error.set('Ingresá el nombre del parámetro o secreto.');
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.execResult.set(null);
    this.busyText.set(
      `Consultando ${name} de origen ${envB} a destino ${envA}...`,
    );

    const payload = {
      env_a: envA,
      env_b: envB,
      service: this.service(),
      name,
      include_deletes: this.includeDeletes(),
      with_secret: this.withSecret(),
    };

    this.paramsService.diff(payload).then(
      (d) => {
        this.busy.set(false);
        this.initState(d);
        this.sessionUpdate({
          status: 'revisado',
          service: this.service(),
          is_secret: this.withSecret() || !!d.pair,
          diff_json: JSON.stringify(d),
          diff_err: null,
          script: d.script ?? null,
          preview: d.pair ? null : (d.patch_value ?? (d.value_b ?? null)),
          notes: (d.notes ?? []).join('\n'),
        });
      },
      (err) => {
        this.busy.set(false);
        const e = toApiError(err);
        this.error.set(e.message);
        this.sessionUpdate({
          service: this.service(),
          is_secret: this.withSecret(),
          diff_err: e.message,
        });
      },
    );
  }

  private initState(d: ParamsDiffResponse) {
    this.data.set(d);
    this.rows.set([]);
    this.pairState.set(null);

    if (d.pair) {
      this.pairState.set({
        param: { text: d.param_apply ?? '', include: !!d.param_needs_write },
        secret: {
          text: d.secret_apply ?? d.secret_value_a ?? '',
          include: !!(d.secret_needs_write || this.withSecret()),
        },
      });
      if (d.param_needs_write || d.secret_needs_write || this.withSecret()) {
        this.pairOnEdit();
      }
      return;
    }

    const hasChanges = Array.isArray(d.changes) && d.changes.length > 0;
    if (hasChanges && d.is_json) {
      this.rows.set(
        (d.changes as DiffChange[]).map((c) => ({
          path: String(c.path ?? ''),
          op: String(c.op ?? 'set'),
          old: c.old,
          text:
            c.op === 'del'
              ? ''
              : c.new != null && typeof c.new === 'object'
                ? JSON.stringify(c.new, null, 2)
                : c.new == null
                  ? ''
                  : String(c.new),
          include: true,
        })),
      );
      this.onEdit();
      return;
    }

    if (hasChanges && !d.is_json) {
      this.rows.set([
        { path: '$', op: 'set', old: d.value_a, text: d.value_b ?? '', include: true },
      ]);
      this.onEdit();
      return;
    }

    if (d.status === 'missing_in_a' || d.status === 'missing_in_b' || d.status === 'none') {
      this.createTarget.set(d.status === 'missing_in_a' ? 'a' : 'b');
      const suggested =
        d.status === 'missing_in_a'
          ? (d.value_b ?? '')
          : d.status === 'missing_in_b'
            ? (d.value_a ?? '')
            : '';
      this.createValue.set(suggested);
      this.refreshCreateScript();
      return;
    }

    if (d.script && d.status === 'different') {
      this.scriptText.set(d.script);
    }
  }

  private sessionUpdate(fields: Partial<UpdateSessionItemRequest>) {
    const id = this.sessionId();
    const d = this.data();
    if (!id || !d || !d.name) return;
    this.sessionService.updateItem(id, { name: d.name, ...fields }).catch(() => null);
  }

  toggleRow(i: number, checked: boolean) {
    this.rows.update((rs) => rs.map((r, idx) => (idx === i ? { ...r, include: checked } : r)));
    this.onEdit();
  }

  setRowText(i: number, text: string) {
    this.rows.update((rs) => rs.map((r, idx) => (idx === i ? { ...r, text } : r)));
    this.onEdit();
  }

  togglePair(key: PairKey, checked: boolean) {
    this.pairState.update((ps) =>
      ps ? { ...ps, [key]: { ...ps[key], include: checked } } : ps,
    );
    this.pairOnEdit();
  }

  setPairText(key: PairKey, text: string) {
    this.pairState.update((ps) =>
      ps ? { ...ps, [key]: { ...ps[key], text } } : ps,
    );
    this.pairOnEdit();
  }

  pairText(key: PairKey): string {
    return this.pairState()?.[key]?.text ?? '';
  }

  private schedule() {
    if (this.applyTimer) clearTimeout(this.applyTimer);
    this.applyTimer = setTimeout(() => {
      const seq = ++this.applySeq;
      const d = this.data();
      if (!d) return;
      this.paramsService
        .apply({
          env_a: d.env_a,
          env_b: d.env_b,
          service: d.service,
          name: d.name,
          new_value: this.serialized(),
          value_type: d.value_type_b || 'String',
        })
        .then(
          (body) => {
            if (seq !== this.applySeq) return;
            this.scriptText.set(body.script);
          },
          () => {
            if (seq !== this.applySeq) return;
            this.scriptText.set('No se pudo generar el comando.');
          },
        );
    }, 350);
  }

  onEdit() {
    this.previewText.set(this.serialized());
    this.schedule();
  }

  private pairPreviewText(): string {
    const ps = this.pairState();
    if (!ps) return '';
    const parts = [];
    if (ps.secret.include) {
      parts.push(`Secreto resultante (Secrets Manager):\n${ps.secret.text}`);
    }
    if (ps.param.include) {
      parts.push(`Parámetro resultante (SSM):\n${ps.param.text}`);
    }
    return parts.join('\n\n');
  }

  pairOnEdit() {
    this.pairPreview.set(this.pairPreviewText());
    if (this.applyTimer) clearTimeout(this.applyTimer);
    this.applyTimer = setTimeout(() => {
      const seq = ++this.applySeq;
      const d = this.data();
      const ps = this.pairState();
      if (!d || !ps) return;
      this.paramsService
        .apply({
          env_a: d.env_a,
          env_b: d.env_b,
          service: d.service,
          name: d.name,
          with_secret: true,
          write_secret: ps.secret.include,
          write_param: ps.param.include,
          new_value: ps.param.include ? ps.param.text : (d.value_a ?? ''),
          value_type: d.value_type_b || 'String',
          new_secret_value: ps.secret.include ? ps.secret.text : (d.secret_value_a ?? ''),
        })
        .then(
          (body) => {
            if (seq !== this.applySeq) return;
            this.scriptText.set(body.script);
          },
          () => {
            if (seq !== this.applySeq) return;
            this.scriptText.set('No se pudo generar el comando.');
          },
        );
    }, 350);
  }

  scheduleCreateScript() {
    if (this.applyTimer) clearTimeout(this.applyTimer);
    this.applyTimer = setTimeout(() => this.refreshCreateScript(), 350);
  }

  private refreshCreateScript() {
    const seq = ++this.applySeq;
    const d = this.data();
    if (!d) return;
    const value = this.createValue();
    this.paramsService
      .apply({
        env_a: d.env_a,
        env_b: d.env_b,
        service: d.service,
        name: d.name,
        new_value: value,
        value_type: d.value_type_b || 'String',
        target: this.createTarget(),
      })
      .then(
        (body) => {
          if (seq !== this.applySeq) return;
          this.scriptText.set(body.script || 'Escribí un valor para generar el comando.');
        },
        () => {
          if (seq !== this.applySeq) return;
          this.scriptText.set('No se pudo generar el comando.');
        },
      );
  }

  private execSuccess(notes: string | null) {
    const d = this.data();
    this.sessionUpdate({
      status: 'aplicado',
      service: d!.pair ? 'ssm' : this.service(),
      is_secret: !!d!.pair,
      script: this.scriptText(),
      notes: notes ?? this.execResult()?.text ?? null,
    });
    setTimeout(() => this.compare(), 700);
  }

  execUpdate(newValue: string) {
    const d = this.data();
    if (!d) return;
    if (!confirm(`¿Ejecutar la actualización de ${d.name} en ${d.env_a}?`)) return;
    this.execResult.set({ text: 'Ejecutando…', isError: false });
    this.busy.set(true);
    this.paramsService
      .applyExecute({
        env_a: d.env_a,
        env_b: d.env_b,
        service: d.service,
        op: 'update',
        target: 'a',
        name: d.name,
        new_value: newValue,
        value_type: d.value_type_b || 'String',
        confirm: true,
      })
      .then(
        (body) => {
          this.busy.set(false);
          this.execResult.set({ text: body.message, isError: false });
          this.execSuccess(body.message);
        },
        (err) => {
          this.busy.set(false);
          this.execResult.set({ text: toApiError(err).message, isError: true });
        },
      );
  }

  execDelete() {
    const d = this.data();
    if (!d) return;
    if (!confirm(`¿Eliminar DEFINITIVAMENTE ${d.name} en ${d.env_a}?\nNo se puede deshacer.`)) return;
    this.execResult.set({ text: 'Ejecutando…', isError: false });
    this.busy.set(true);
    this.paramsService
      .applyExecute({
        env_a: d.env_a,
        env_b: d.env_b,
        service: d.service,
        op: 'delete',
        target: 'a',
        name: d.name,
        confirm: true,
      })
      .then(
        (body) => {
          this.busy.set(false);
          this.execResult.set({ text: body.message, isError: false });
          this.execSuccess(body.message);
        },
        (err) => {
          this.busy.set(false);
          this.execResult.set({ text: toApiError(err).message, isError: true });
        },
      );
  }

  execPair() {
    const d = this.data();
    const ps = this.pairState();
    if (!d || !ps) return;
    const stepsMsg = [
      ps.secret.include ? 'Paso 1 — actualizar el SECRETO en Secrets Manager' : '',
      ps.param.include ? 'Paso 2 — actualizar el PARÁMETRO en SSM' : '',
    ]
      .filter(Boolean)
      .join('\n');
    if (
      !confirm(
        `¿Sincronizar ${d.name} en ${d.env_a}?\n${stepsMsg}\n\n` +
        'Se ejecutan en ese orden: nunca en una sola operación.',
      )
    ) {
      return;
    }
    this.execResult.set({ text: 'Ejecutando…', isError: false });
    this.busy.set(true);
    this.paramsService
      .applyExecute({
        env_a: d.env_a,
        env_b: d.env_b,
        service: d.service,
        op: 'update',
        target: 'a',
        name: d.name,
        with_secret: true,
        write_secret: ps.secret.include,
        write_param: ps.param.include,
        new_value: ps.param.include ? ps.param.text : (d.value_a ?? ''),
        value_type: d.value_type_b || 'String',
        new_secret_value: ps.secret.include ? ps.secret.text : (d.secret_value_a ?? ''),
        confirm: true,
      })
      .then(
        (body) => {
          this.busy.set(false);
          const lines = (body.steps ?? []).map((s) => `✓ ${s.message}`).join('\n');
          this.execResult.set({ text: lines, isError: false });
          this.execSuccess(
            (body.steps ?? []).map((s) => s.message).join(' · ') || null,
          );
        },
        (err) => {
          this.busy.set(false);
          this.execResult.set({ text: toApiError(err).message, isError: true });
        },
      );
  }

  execCreate() {
    const d = this.data();
    if (!d) return;
    const value = this.createValue();
    if (value.trim() === '') {
      this.execResult.set({ text: 'Escribí un valor para crear el parámetro.', isError: true });
      return;
    }
    const region = this.createRegion();
    const verb = d.status === 'missing_in_a' ? 'actualizar/crear' : 'crear';
    if (!confirm(`¿${verb} ${d.name} en ${region} con el valor ingresado?`)) return;
    this.execResult.set({ text: 'Creando…', isError: false });
    this.busy.set(true);
    this.paramsService
      .applyExecute({
        env_a: d.env_a,
        env_b: d.env_b,
        service: d.service,
        op: 'update',
        target: this.createTarget(),
        name: d.name,
        new_value: value,
        value_type: d.value_type_b || 'String',
        confirm: true,
      })
      .then(
        (body) => {
          this.busy.set(false);
          this.execResult.set({ text: body.message, isError: false });
          this.execSuccess(body.message);
        },
        (err) => {
          this.busy.set(false);
          this.execResult.set({ text: toApiError(err).message, isError: true });
        },
      );
  }

  pre(v: string): string {
    return v || '—';
  }

  preScript(): string {
    return this.pre(this.data()?.script ?? '');
  }
}