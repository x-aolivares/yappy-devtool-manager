import { Component, computed, DestroyRef, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ThemeService } from './core/services/theme.service';
import { AwsSessionService } from './core/services/aws-session.service';
import { NAV_SECTIONS } from './core/nav';
import { NoticeModal } from './components/notice-modal/notice-modal';

type AwsStatusTone = 'checking' | 'ok' | 'warn' | 'danger';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, NoticeModal],
  styleUrl: './app.scss',
  templateUrl: './app.html',
})
export class App {
  readonly theme = inject(ThemeService);
  readonly awsSession = inject(AwsSessionService);
  readonly sections = NAV_SECTIONS;

  /** Minutos o menos de vida restante: vale la pena renovar antes de que corte. */
  private static readonly WARN_SECONDS = 15 * 60;

  constructor() {
    const destroyRef = inject(DestroyRef);
    void this.awsSession.refreshStatus();

    // Dos relojes con trabajos distintos: el contador se mueve cada 5s para
    // que el paso a "Sin autenticar" no espere un minuto, y el estado se
    // consulta cada 60s. Antes ambos eran lo mismo y el tiempo quedaba
    // congelado entre consultas.
    const clock = window.setInterval(() => this.awsSession.tick(), 5_000);
    const refresh = window.setInterval(() => void this.awsSession.refreshStatus(), 60_000);

    // Una pestaña en background tiene el timer suspendido: al volver hay que
    // sincronizar el reloj y pedir el estado, no mostrar el de hace una hora.
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return;
      this.awsSession.tick();
      void this.awsSession.refreshStatus();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);

    destroyRef.onDestroy(() => {
      window.clearInterval(clock);
      window.clearInterval(refresh);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    });
  }

  readonly awsPromptOpen = computed(() => !!this.awsSession.prompt() && this.awsSession.loginState()?.status !== 'authorized');
  readonly awsAuthenticatedOpen = computed(() => this.awsSession.loginState()?.status === 'authorized');

  readonly awsStatusTone = computed<AwsStatusTone>(() => {
    const status = this.awsSession.status();
    if (!status) return 'checking';
    const remaining = this.awsSession.remainingSeconds();
    if (!status.active || remaining === 0) return 'danger';
    if (remaining !== null && remaining <= App.WARN_SECONDS) return 'warn';
    return 'ok';
  });

  readonly awsStatusLabel = computed(() => {
    switch (this.awsStatusTone()) {
      case 'checking':
        return 'Comprobando...';
      case 'ok':
        return 'Activa';
      case 'warn':
        return 'Por vencer';
      case 'danger':
        return 'Sin autenticar';
    }
  });

  readonly awsStatusMeta = computed(() => {
    const status = this.awsSession.status();
    if (!status) return null;
    const remaining = this.awsSession.remainingSeconds();
    if (!status.active || remaining === 0) {
      return status.message ?? 'La sesión de AWS no está activa.';
    }
    const parts: string[] = [];
    if (status.profile) parts.push(`Perfil: ${status.profile}`);
    if (status.expires_at) parts.push(this.formatExpiry(status.expires_at, remaining));
    return parts.join('\n');
  });

  readonly awsAuthenticatedMeta = computed(() => {
    const status = this.awsSession.status();
    if (!status) return null;
    if (!status.active) return status.message ?? null;
    return status.expires_at ? this.formatExpiry(status.expires_at, this.awsSession.remainingSeconds()) : null;
  });

  /** El modal cambia de contexto: renovar no es lo mismo que autenticarse. */
  readonly awsPromptTitle = computed(() =>
    this.awsSession.prompt()?.renew ? 'Renovar sesión AWS' : 'Sesión de AWS no autenticada',
  );

  readonly awsPromptConfirmLabel = computed(() =>
    this.awsSession.prompt()?.renew ? 'Renovar' : 'Iniciar sesión AWS',
  );

  readonly awsActionLabel = computed(() => {
    if (this.awsSession.loginBusy()) return 'Esperando autorización…';
    if (!this.awsSession.status()) return 'Iniciar sesión AWS';
    return this.awsStatusTone() === 'danger' ? 'Iniciar sesión AWS' : 'Renovar sesión';
  });

  private formatExpiry(expiresAt: string, remainingSeconds: number | null): string {
    const expires = new Date(expiresAt);
    const hh = String(expires.getHours()).padStart(2, '0');
    const mm = String(expires.getMinutes()).padStart(2, '0');
    if (remainingSeconds === null) return `Vence: ${hh}:${mm}`;
    // `ceil` para no mostrar "en ~0 min" mientras todavía quedan segundos.
    const mins = Math.max(1, Math.ceil(remainingSeconds / 60));
    return `Vence: ${hh}:${mm} (en ~${mins} min)`;
  }

  async startAwsLogin(): Promise<void> {
    await this.awsSession.startLogin();
  }

  dismissAwsPrompt(): void {
    this.awsSession.dismissPrompt();
  }

  closeAwsAuthenticated(): void {
    this.awsSession.dismissPrompt();
  }
}
