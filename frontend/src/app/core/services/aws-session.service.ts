import { Injectable, computed, inject, signal } from '@angular/core';
import { Api } from '../../api-gen/api';
import { awsSessionLoginPoll, awsSessionLoginStart, awsSessionStatus } from '../../api-gen/functions';
import {
  AwsSessionLoginPollResponse,
  AwsSessionLoginStartResponse,
  AwsSessionStatusResponse,
} from '../../api-gen/models';

export interface AwsSessionPrompt {
  message?: string | null;
  profile?: string | null;
  /** `true` cuando se pidió volver a autorizar una sesión todavía activa. */
  renew?: boolean | null;
}

@Injectable({ providedIn: 'root' })
export class AwsSessionService {
  private readonly api = inject(Api);

  readonly status = signal<AwsSessionStatusResponse | null>(null);
  /**
   * Cuándo llegó la respuesta. El contador no compara `expires_at` con el reloj
   * del navegador —que puede estar corrido— sino que descuenta desde acá los
   * segundos que informó el backend.
   */
  readonly statusFetchedAt = signal<number | null>(null);
  readonly statusLoading = signal(false);
  readonly prompt = signal<AwsSessionPrompt | null>(null);
  readonly login = signal<AwsSessionLoginStartResponse | null>(null);
  readonly loginState = signal<AwsSessionLoginPollResponse | null>(null);
  readonly loginBusy = signal(false);
  readonly loginError = signal<string | null>(null);

  /** Lo mueve el reloj del `App`; el servicio no arma un segundo intervalo. */
  private readonly now = signal(Date.now());

  readonly remainingSeconds = computed(() => {
    const status = this.status();
    const fetchedAt = this.statusFetchedAt();
    if (!status?.active || fetchedAt === null || status.expires_in_seconds == null) return null;
    const elapsed = Math.max(0, (this.now() - fetchedAt) / 1000);
    return Math.max(0, Math.round(status.expires_in_seconds - elapsed));
  });

  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private dismissed = false;

  /** Un tick del reloj compartido: recalcula el contador sin pedir estado. */
  tick(): void {
    this.now.set(Date.now());
  }

  async refreshStatus(): Promise<AwsSessionStatusResponse | null> {
    this.statusLoading.set(true);
    try {
      const status = await this.api.invoke(awsSessionStatus, {});
      this.status.set(status);
      this.statusFetchedAt.set(Date.now());
      this.tick();
      return status;
    } catch {
      return null;
    } finally {
      this.statusLoading.set(false);
    }
  }

  requestReauth(prompt: AwsSessionPrompt = {}): void {
    // Si ya hay un modal abierto o un login en curso, no apilar otro.
    if (this.prompt() || this.loginBusy()) return;
    this.dismissed = false;
    this.login.set(null);
    this.loginState.set(null);
    this.loginError.set(null);
    // Un 401 viene de una sesión que ya no sirve: nunca es una renovación.
    this.prompt.set({ ...prompt, renew: false });
  }

  dismissPrompt(): void {
    const wasWaiting = this.loginBusy() && this.loginState()?.status !== 'authorized';
    this.prompt.set(null);
    this.login.set(null);
    this.loginState.set(null);
    this.loginError.set(null);
    this.loginBusy.set(false);
    this.stopPolling();
    // Cancelar es una decisión del usuario: no insistir hasta la próxima acción AWS.
    this.dismissed = true;
    // El device flow del backend sigue hasta expirar; al cerrar mientras se
    // esperaba autorización, se consulta el estado para no dejar el sidebar
    // con un dato viejo hasta el próximo tick.
    if (wasWaiting) void this.refreshStatus();
  }

  /** Permite que una nueva falla AWS vuelva a mostrar el modal. */
  clearDismissed(): void {
    this.dismissed = false;
  }

  isDismissed(): boolean {
    return this.dismissed;
  }

  async startLogin(): Promise<void> {
    if (this.loginBusy()) {
      // Ya hay un intento en curso: en vez de no hacer nada —un segundo clic
      // en "Renovar" era un silencio total— se reabre la URL de verificación.
      // Este `window.open` corre dentro del gesto del click, así que el
      // navegador no lo bloquea.
      const current = this.login();
      if (current) this.openVerification(current);
      return;
    }
    this.loginBusy.set(true);
    this.loginError.set(null);
    this.loginState.set(null);
    try {
      const login = await this.api.invoke(awsSessionLoginStart, {});
      this.login.set(login);
      if (!this.prompt()) {
        this.prompt.set({
          profile: login.profile,
          message: null,
          renew: !!this.status()?.active,
        });
      }
      this.openVerification(login);
      this.startPolling(login.login_id);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.loginError.set(message);
      this.loginBusy.set(false);
      if (!this.prompt()) {
        this.prompt.set({ message: null });
      }
    }
  }

  private openVerification(login: AwsSessionLoginStartResponse): void {
    const url = login.verification_uri_complete || login.verification_uri;
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
  }

  private startPolling(loginId: string): void {
    this.stopPolling();
    this.pollTimer = setInterval(() => {
      void this.poll(loginId);
    }, 2500);
    void this.poll(loginId);
  }

  private async poll(loginId: string): Promise<void> {
    try {
      const state = await this.api.invoke(awsSessionLoginPoll, { login_id: loginId });
      this.loginState.set(state);
      if (state.status === 'authorized') {
        this.stopPolling();
        this.loginBusy.set(false);
        // No copiar el `expires_in_seconds` anterior: era el de la sesión
        // vencida y el contador arrancaría ya vencido. Se muestra el dato
        // mínimo del poll y enseguida se pide el estado real al backend.
        this.status.set({
          active: state.active,
          profile: state.profile,
          expires_at: state.expires_at ?? null,
          sso_session: this.status()?.sso_session ?? null,
          start_url: this.status()?.start_url ?? null,
          expires_in_seconds: null,
          message: state.message ?? null,
        });
        this.statusFetchedAt.set(null);
        await this.refreshStatus();
        return;
      }
      if (state.status === 'expired' || state.status === 'error') {
        this.stopPolling();
        this.loginBusy.set(false);
        this.loginError.set(state.message ?? 'No se pudo completar la autenticación.');
      }
    } catch (err) {
      // Errores transitorios de polling no cortan el intento; el backend sigue siendo la fuente.
      const message = err instanceof Error ? err.message : String(err);
      this.loginError.set(message);
    }
  }

  private stopPolling(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }
}