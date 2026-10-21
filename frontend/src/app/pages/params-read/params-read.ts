import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import {
  CreateMultiParamsRequest,
  EnvironmentInfo,
  ParamsReadResponse,
  ReadEntryResultInfo,
} from '../../api-gen/models';
import { EnvironmentService } from '../../core/services/environment.service';
import { ParamsService } from '../../core/services/params.service';
import { SessionService } from '../../core/services/session.service';
import { toApiError } from '../../core/services/api-error';
import { CancelSlot, isCancellation } from '../../core/cancel';
import { awsEnvironments, formatValue, isJsonDocument } from '../../core/format';
import { SsmValueType, toValueType } from '../../core/ssm';
import { EnvControls, PARAM_SERVICES } from '../../components/env-controls/env-controls';
import { StatusBadge } from '../../components/status-badge/status-badge';
import { BusyModal } from '../../components/busy-modal/busy-modal';
import { NoticeModal } from '../../components/notice-modal/notice-modal';

interface EnvPanel {
  env: string;
  region?: string | null;
  profile?: string | null;
  /** Contenido tal como está el cuadro de edición. */
  value: string;
  valueType: SsmValueType;
  /** El valor crudo es un documento JSON: va en `textarea`, no en `input`. */
  isJson: boolean;
  ok: boolean;
  error: string | null;
  differs: boolean;
}

/**
 * Leer / actualizar un parámetro o secreto en N ambientes.
 *
 * La selección sale de los grupos de píldoras (nada hardcodeado); del primer ambiente se
 * muestra el valor como caja editable y `Actualizar` escribe SÓLO ese ambiente
 * vía params/multi con `envs: [env]`. Con dos o más ambientes marcados se arma
 * la sesión de trabajo (origen = primero, destino = segundo).
 */
@Component({
  selector: 'app-params-read-page',
  imports: [EnvControls, StatusBadge, BusyModal, NoticeModal],
  templateUrl: './params-read.html',
})
export class ParamsReadPage {
  private readonly envService = inject(EnvironmentService);
  private readonly paramsService = inject(ParamsService);
  private readonly sessionService = inject(SessionService);

  readonly environments = signal<EnvironmentInfo[] | null>(null);
  /**
   * `local` no es un ambiente de AWS: esta página solo ofrece los que lo son. La
   * búsqueda de metadatos del env elegido sigue usando la lista completa.
   */
  readonly awsEnvironments = computed(() => awsEnvironments(this.environments()));
  readonly envs = signal<string[]>([]);
  readonly service = signal('ssm');
  readonly name = signal('');
  readonly sessionAlias = signal('');
  readonly requireAlias = signal(false);

  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly panels = signal<EnvPanel[]>([]);
  readonly writingEnv = signal<string | null>(null);
  readonly writeStatus = signal<Record<string, { ok: boolean; message: string }>>({});
  /** Ambiente cuyo valor se acaba de copiar, para el "Copiado" del botón. */
  readonly copiedEnv = signal<string | null>(null);

  /**
   * La búsqueda en vuelo. N ambientes son N llamadas a SSM al mismo tiempo y cada
   * una puede colgarse con un token que no responde: con cuatro marcados hay que
   * poder abandonar la búsqueda a medio camino.
   */
  private readonly op = new CancelSlot();

  readonly services = PARAM_SERVICES;

  private copyTimer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    const q = new URLSearchParams(location.search);
    const sessionAlias = q.get('alias') ?? q.get('session_alias') ?? '';
    const fromSession =
      q.get('from_session') === '1' || q.get('session_id') !== null || q.get('session') !== null;
    this.requireAlias.set(fromSession);
    if (sessionAlias) {
      this.sessionAlias.set(sessionAlias);
    }

    // El aviso de "Copiado" se desarma solo; si se navega antes, el timer queda
    // pendiente y termina escribiendo en una signal de un componente destruido.
    inject(DestroyRef).onDestroy(() => {
      if (this.copyTimer) clearTimeout(this.copyTimer);
    });

    this.envService.list().then(
      (envs) => this.environments.set(envs.environments),
      (err) => this.error.set('No se pudieron cargar los ambientes: ' + toApiError(err).message),
    );
  }

  /**
   * Copia el valor del panel al portapapeles.
   *
   * Se copia `panel.value`, que es lo que muestra la caja: con JSON es el texto
   * indentado, no el crudo que llegó del backend. Copiar el crudo sorprendería
   * a quien pega en una terminal.
   *
   * El navigator.clipboard no existe fuera de un contexto seguro —abrir la web por
   * IP de la red, por ejemplo— y ahí `writeText` ni siquiera está. Por eso el caso
   * "no hay portapapeles" se separa del "el navegador lo negó": son dos fallos
   * distintos y el usuario necesita un mensaje, no un botón que finge.
   */
  async copyParameter(panel: EnvPanel): Promise<void> {
    const clipboard = navigator.clipboard;
    if (!clipboard) {
      this.error.set(
        'El navegador no expone el portapapeles acá. Abrí la web en localhost o en https.',
      );
      return;
    }
    try {
      await clipboard.writeText(panel.value);
    } catch {
      this.error.set('El navegador no dejó copiar al portapapeles.');
      return;
    }
    this.copiedEnv.set(panel.env);
    if (this.copyTimer) clearTimeout(this.copyTimer);
    this.copyTimer = setTimeout(() => this.copiedEnv.set(null), 2000);
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

    const run = this.op.begin();
    this.paramsService.read(envs, [name], run).then(
      (d) => {
        if (!this.op.finish(run)) return;
        this.busy.set(false);
        this.buildPanels(d, envs);
        this.ensureSession(name);
      },
      (err) => {
        if (!this.op.finish(run)) return;
        this.busy.set(false);
        // Cortar la espera no es un fallo: el usuario lo pidió. Ver `core/cancel`.
        if (isCancellation(err)) return;
        this.error.set(toApiError(err).message);
      },
    );
  }

  /**
   * Dejar de esperar la búsqueda.
   *
   * Los paneles que ya estaban quedan como estaban: describen la búsqueda
   * anterior, que sigue siendo la última que se completó.
   */
  cancelBusy(): void {
    if (!this.op.cancel()) return;
    this.busy.set(false);
  }

  private buildPanels(res: ParamsReadResponse, envs: string[]): void {
    const byEnv = new Map<string, ReadEntryResultInfo[]>();
    for (const r of res.results) {
      const env = r.env ?? '';
      if (!env) continue;
      if (!byEnv.has(env)) byEnv.set(env, []);
      byEnv.get(env)!.push(r);
    }

    const reference = byEnv.get(envs[0] ?? '');
    const referenceValues = new Map(
      (reference ?? []).map((r) => [r.key, r.ok ? (r.value ?? '').trim() : null]),
    );

    this.panels.set(
      envs
        .filter((env) => byEnv.has(env))
        .flatMap((env): EnvPanel[] => {
          const r = byEnv.get(env)?.[0];
          if (!r) return [];
          const rawValue = r.ok ? formatValue(r.value) : r.error || 'No existe el parámetro.';
          const meta = (this.environments() ?? []).find((e) => e.env === env);
          return [
            {
              env,
              region: meta?.region,
              profile: meta?.profile,
              value: rawValue,
              valueType: toValueType(r.value_type),
              isJson: r.ok && isJsonDocument(r.value),
              ok: r.ok,
              error: r.error ?? null,
              differs:
                r.ok && (referenceValues.get(r.key) ?? null) !== (r.value ?? '').trim(),
            },
          ];
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
        [panel.env]: {
          ok: false,
          message: 'No podés guardar un valor vacío; borrá el parámetro desde Crear.',
        },
      }));
      return;
    }
    // El diálogo es asíncrono, así que el click no escribe: deja pendiente lo que
    // hay que mandar y devuelve. El guardado pasa por `confirmar()`, que corre
    // cuando el usuario confirma. El texto va en párrafos porque el cuerpo del
    // modal es HTML y un `\n` adentro de un `<p>` es un espacio.
    this.pendingUpdate.set({
      titulo: `¿Actualizar '${name}' en ${panel.env}?`,
      parrafos: ['Se escribe el valor del cuadro, sobrescribe el actual.'],
      panel,
    });
  }

  /** Confirmó en el modal: recién acá se arma el request y se escribe. */
  confirmar(): void {
    const p = this.pendingUpdate();
    this.pendingUpdate.set(null);
    if (!p) return;

    const panel = p.panel;
    const name = this.name().trim();
    const value = panel.value;

    const multi: CreateMultiParamsRequest =
      panel.valueType === 'SecureString'
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
            value_type: toValueType(panel.valueType),
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

  /** ✕, Escape, backdrop o "Cancelar": no se escribe nada. */
  cancelar(): void {
    this.pendingUpdate.set(null);
  }

  /**
   * La actualización pendiente de confirmar. Guarda el panel entero, y no sólo
   * el ambiente: entre el click y el clic en "Actualizar" el usuario puede editar
   * el cuadro, y lo que se escribe tiene que ser lo que se le mostró.
   */
  readonly pendingUpdate = signal<{ titulo: string; parrafos: string[]; panel: EnvPanel } | null>(
    null,
  );

  private ensureSession(name: string) {
    const [origin = '', destination = ''] = this.envs();
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
    // La sesión se crea aunque esta pantalla no la muestre: queda en el historial
    // de Sesiones y `reuse: true` evita duplicarla si se busca dos veces. Acá el
    // resultado es la tabla de valores, no un link. Si el alta falla no se avisa:
    // leer los valores no depende de eso.
    this.sessionService.create(payload).catch(() => null);
  }

  protected readonly formatValue = formatValue;
}
