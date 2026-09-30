/**
 * Lookup de parámetros por ambiente.
 *
 * Los parámetros salen de `config/env.<nombre>` (eso incluye los `YAPPY_*` del
 * entorno, que tienen precedencia). La detección de JSON es solo de
 * presentación: el backend ya clasifica cada valor en `is_json`.
 *
 * El valor crudo de un parámetro ES el nombre del secreto en Secrets Manager.
 * Por eso la resolución es siempre una acción explícita del usuario — no hay
 * búsqueda, ni listado, ni coincidencia por valor.
 */
import { Component, computed, effect, inject, signal } from '@angular/core';
import { ApiService } from '../../core/api';
import { EnvContext } from '../../core/env-context';
import { Parameter, ResolvedSecret } from '../../core/models';
import { Alert, describe } from '../../shared/alert';

@Component({
  selector: 'app-parameters',
  imports: [Alert],
  templateUrl: './parameters.html',
  styleUrl: './parameters.css',
})
export class Parameters {
  private readonly api = inject(ApiService);
  private readonly ctx = inject(EnvContext);

  protected readonly env = this.ctx.env;
  protected readonly hasEnv = this.ctx.hasEnv;

  protected readonly parameters = signal<Parameter[]>([]);
  protected readonly loading = signal(false);
  protected readonly error = signal<{
    code: string;
    message: string;
    detail: string | null;
  } | null>(null);

  protected readonly filter = signal('');
  protected readonly selectedKey = signal<string | null>(null);
  protected readonly secret = signal<ResolvedSecret | null>(null);
  protected readonly resolving = signal(false);
  protected readonly secretError = signal<{
    code: string;
    message: string;
    detail: string | null;
  } | null>(null);

  protected readonly jsonCount = computed(
    () => this.parameters().filter((p) => p.is_json).length,
  );

  /** El valor crudo del parámetro es el nombre del secreto, sin convención. */
  protected readonly secretName = computed(
    () =>
      this.parameters().find((p) => p.key === this.selectedKey())?.value ?? '',
  );

  protected readonly visible = computed(() => {
    const needle = this.filter().trim().toLowerCase();
    const all = this.parameters();
    if (needle === '') return all;
    // Filtra por clave y por valor: el caso de uso real es "busco este RDS host
    // o este user", no solo "busco el nombre de la clave".
    return all.filter(
      (p) =>
        p.key.toLowerCase().includes(needle) ||
        p.value.toLowerCase().includes(needle),
    );
  });

  constructor() {
    // Recarga al cambiar de ambiente: los parámetros son por ambiente, no hay
    // sentido de cachear de uno a otro.
    effect(() => {
      const env = this.env();
      this.selectedKey.set(null);
      this.secret.set(null);
      this.secretError.set(null);
      this.filter.set('');
      if (env) this.load();
    });
  }

  protected load(): void {
    const env = this.env();
    if (!env) return;

    this.loading.set(true);
    this.error.set(null);

    this.api.safe(this.api.listParameters(env)).subscribe({
      next: (list) => {
        this.parameters.set(list);
        this.loading.set(false);
      },
      error: (err: unknown) => {
        this.parameters.set([]);
        this.error.set(describe(err));
        this.loading.set(false);
      },
    });
  }

  protected setFilter(event: Event): void {
    this.filter.set((event.target as HTMLInputElement).value);
  }

  protected select(key: string): void {
    this.selectedKey.set(key);
    this.secret.set(null);
    this.secretError.set(null);
  }

  protected resolve(): void {
    const env = this.env();
    const key = this.selectedKey();
    if (!env || !key) return;

    this.resolving.set(true);
    this.secretError.set(null);

    this.api.safe(this.api.resolveSecret(env, key)).subscribe({
      next: (resolved) => {
        this.secret.set(resolved);
        this.resolving.set(false);
      },
      error: (err: unknown) => {
        this.secret.set(null);
        this.secretError.set(describe(err));
        this.resolving.set(false);
      },
    });
  }

  protected clearSecret(): void {
    this.secret.set(null);
    this.secretError.set(null);
  }

  protected pretty(value: string): string {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }

  protected copy(text: string): void {
    void navigator.clipboard?.writeText(text);
  }

  protected trackKey(_index: number, p: Parameter): string {
    return p.key;
  }
}
