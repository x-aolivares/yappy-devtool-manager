import { Injectable, inject, signal } from '@angular/core';
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
}

@Injectable({ providedIn: 'root' })
export class AwsSessionService {
  private readonly api = inject(Api);

  readonly status = signal<AwsSessionStatusResponse | null>(null);
  readonly statusLoading = signal(false);
  readonly prompt = signal<AwsSessionPrompt | null>(null);
  readonly login = signal<AwsSessionLoginStartResponse | null>(null);
  readonly loginState = signal<AwsSessionLoginPollResponse | null>(null);
  readonly loginBusy = signal(false);
  readonly loginError = signal<string | null>(null);

  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private dismissed = false;

  async refreshStatus(): Promise<AwsSessionStatusResponse | null> {
    this.statusLoading.set(true);
    try {
      const status = await this.api.invoke(awsSessionStatus, {});
      this.status.set(status);
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
    this.prompt.set(prompt);
  }

  dismissPrompt(): void {
    this.prompt.set(null);
    this.login.set(null);
    this.loginState.set(null);
    this.loginError.set(null);
    this.loginBusy.set(false);
    this.stopPolling();
    // Cancelar es una decisión del usuario: no insistir hasta la próxima acción AWS.
    this.dismissed = true;
  }

  /** Permite que una nueva falla AWS vuelva a mostrar el modal. */
  clearDismissed(): void {
    this.dismissed = false;
  }

  isDismissed(): boolean {
    return this.dismissed;
  }

  async startLogin(): Promise<void> {
    if (this.loginBusy()) return;
    this.loginBusy.set(true);
    this.loginError.set(null);
    this.loginState.set(null);
    try {
      const login = await this.api.invoke(awsSessionLoginStart, {});
      this.login.set(login);
      if (!this.prompt()) {
        this.prompt.set({ profile: login.profile, message: null });
      }
      window.open(login.verification_uri_complete || login.verification_uri, '_blank', 'noopener,noreferrer');
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
        this.status.set({
          active: state.active,
          profile: state.profile,
          expires_at: state.expires_at ?? null,
          sso_session: this.status()?.sso_session ?? null,
          start_url: this.status()?.start_url ?? null,
          expires_in_seconds: this.status()?.expires_in_seconds ?? null,
          message: state.message ?? null,
        });
        void this.refreshStatus();
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