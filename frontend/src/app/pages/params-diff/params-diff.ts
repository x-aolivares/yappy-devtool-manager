import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { EnvironmentInfo } from '../../api-gen/models';
import { EnvironmentService } from '../../core/services/environment.service';
import { ParamsService } from '../../core/services/params.service';
import { SessionService } from '../../core/services/session.service';
import { toApiError } from '../../core/services/api-error';
import { CancelSlot, isCancellation } from '../../core/cancel';
import { awsEnvironments, formatValue } from '../../core/format';
import { serializeMerged } from '../../core/params-merge';
import type { ChangeRow } from '../../core/params-merge';
import { ParamsDiffResponse, UpdateSessionItemRequest } from '../../api-gen/models';
import { BusyModal } from '../../components/busy-modal/busy-modal';
import { RegionControls } from '../../components/region-controls';
import { StatusBadge } from '../../components/status-badge';
import { CopyButton } from '../../components/copy-button/copy-button';
import { PaginationBar } from '../../components/pagination-bar/pagination-bar';
import { paginate } from '../../core/paginate';

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
  imports: [
    RouterLink,
    RegionControls,
    StatusBadge,
    BusyModal,
    CopyButton,
    PaginationBar,
  ],
  templateUrl: './params-diff.html',
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

  /**
   * La comparación en vuelo, para que el botón del modal la corte.
   *
   * Lee el parámetro en las dos regiones y arma el parche: son dos idas a la base
   * de cada ambiente, y con el túnel lento son de las consultas donde más se
   * echa de menos abandonar a mitad de camino.
   */
  private readonly op = new CancelSlot();

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

    const run = this.op.begin();
    this.paramsService.diff(payload, run).then(
      (d) => {
        if (!this.op.finish(run)) return;
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
        if (!this.op.finish(run)) return;
        this.busy.set(false);
        // Cortar la espera no es un fallo: el usuario lo pidió. Ver `core/cancel`.
        if (isCancellation(err)) return;
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

  /**
   * Cortar la comparación en vuelo y dejar la pantalla como estaba.
   *
   * Sin cambios a propósito: los datos que ya estaban siguen describiendo la
   * última comparación que terminó, y la sesión —que se actualiza con cada
   * comparación— no se toca, porque no hubo comparación.
   */
  cancelBusy(): void {
    if (!this.op.cancel()) return;
    this.busy.set(false);
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