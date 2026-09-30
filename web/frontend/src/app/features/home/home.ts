/**
 * Inicio: estado de la sesión y accesos directos.
 *
 * La función real de esta pantalla es una de diagnóstico: por qué una feature
 * no va a funcionar. Lo típico es que el túnel de BD esté caído o que falte el
 * CA de RDS, y ambas cosas son invisibles hasta que se intenta algo.
 */
import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api';
import { EnvContext } from '../../core/env-context';
import { EnvironmentsStore } from '../../core/environments.store';
import {
  ConnectionInfo,
  Environment,
  LocalMysqlStatus,
} from '../../core/models';
import { Alert, describe } from '../../shared/alert';

@Component({
  selector: 'app-home',
  imports: [RouterLink, Alert],
  templateUrl: './home.html',
  styleUrl: './home.css',
})
export class Home {
  private readonly api = inject(ApiService);
  private readonly ctx = inject(EnvContext);
  private readonly store = inject(EnvironmentsStore);

  private readonly envs = this.store.environments;
  protected readonly selected = this.ctx.env;
  protected readonly hasEnv = this.ctx.hasEnv;

  protected readonly connection = signal<ConnectionInfo | null>(null);
  protected readonly connectionError = signal<{
    code: string;
    message: string;
    detail: string | null;
  } | null>(null);
  protected readonly checkingConnection = signal(false);

  protected readonly local = signal<LocalMysqlStatus | null>(null);
  protected readonly localError = signal<string | null>(null);
  protected readonly busyLocal = signal(false);

  protected readonly details = computed<Environment | null>(() => {
    const env = this.selected();
    return this.envs().find((e) => e.name === env) ?? null;
  });

  constructor() {
    this.refreshLocal();
  }

  /** El estado del túnel cambia por fuera de la web, así que se consulta. */
  protected refreshConnection(): void {
    const env = this.selected();
    if (!env) return;

    this.checkingConnection.set(true);
    this.connectionError.set(null);

    this.api.safe(this.api.getConnection(env)).subscribe({
      next: (info) => {
        this.connection.set(info);
        this.checkingConnection.set(false);
      },
      error: (err: unknown) => {
        this.connection.set(null);
        this.connectionError.set(describe(err));
        this.checkingConnection.set(false);
      },
    });
  }

  protected refreshLocal(): void {
    this.api.safe(this.api.localMysqlStatus()).subscribe({
      next: (status) => {
        this.local.set(status);
        this.localError.set(null);
      },
      error: (err: unknown) => {
        this.local.set(null);
        this.localError.set(describe(err).message);
      },
    });
  }

  protected startLocal(): void {
    this.busyLocal.set(true);
    this.api.safe(this.api.startLocalMysql()).subscribe({
      next: (status) => {
        this.local.set(status);
        this.localError.set(null);
        this.busyLocal.set(false);
      },
      error: (err: unknown) => {
        this.localError.set(describe(err).message);
        this.busyLocal.set(false);
        this.refreshLocal();
      },
    });
  }
}
