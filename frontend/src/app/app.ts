import { Component, computed, DestroyRef, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ThemeService } from './core/services/theme.service';
import { AwsSessionService } from './core/services/aws-session.service';
import { NAV_SECTIONS } from './core/nav';
import { NoticeModal } from './components/notice-modal/notice-modal';

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

  /** Reloj propio: sin él, el tiempo restante se calcula una sola vez y se congela. */
  private readonly now = signal(Date.now());

  constructor() {
    const destroyRef = inject(DestroyRef);
    void this.awsSession.refreshStatus();
    // Cada minuto se recalcula el tiempo restante y se vuelve a consultar el
    // estado: si la sesión vence con la pestaña abierta, el sidebar tiene que
    // pasar a "Sin autenticar" sin esperar un reload.
    const tick = window.setInterval(() => {
      this.now.set(Date.now());
      void this.awsSession.refreshStatus();
    }, 60_000);
    destroyRef.onDestroy(() => window.clearInterval(tick));
  }

  readonly awsPromptOpen = computed(() => !!this.awsSession.prompt() && this.awsSession.loginState()?.status !== 'authorized');
  readonly awsAuthenticatedOpen = computed(() => this.awsSession.loginState()?.status === 'authorized');

  readonly awsStatusLabel = computed(() => {
    const status = this.awsSession.status();
    if (!status) return 'Comprobando...';
    return status.active ? 'Activa' : 'Sin autenticar';
  });

  readonly awsStatusMeta = computed(() => {
    const status = this.awsSession.status();
    if (!status || !status.active) return null;
    const parts: string[] = [];
    if (status.profile) parts.push(`Perfil: ${status.profile}`);
    if (status.expires_at) parts.push(this.formatExpiry(status.expires_at, this.now()));
    return parts.join('\n');
  });

  readonly awsAuthenticatedMeta = computed(() => {
    const status = this.awsSession.status();
    if (!status) return null;
    return status.expires_at ? this.formatExpiry(status.expires_at, this.now()) : null;
  });

  private formatExpiry(expiresAt: string, now: number): string {
    const expires = new Date(expiresAt);
    const mins = Math.max(0, Math.round((expires.getTime() - now) / 60000));
    const hh = String(expires.getHours()).padStart(2, '0');
    const mm = String(expires.getMinutes()).padStart(2, '0');
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
