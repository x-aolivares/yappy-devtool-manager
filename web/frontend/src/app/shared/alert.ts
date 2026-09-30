/**
 * Barra de alertas para los errores de dominio.
 *
 * Centraliza el render del payload `{code, message, detail}` de
 * `error_handlers.py`. Cada feature guarda su propio mensaje, pero todas usan
 * este componente, así el formato de un error es el mismo en toda la app.
 */
import { Component, input } from '@angular/core';
import { ApiRequestError } from '../core/api';

type Tone = 'error' | 'success' | 'warning' | 'info';

@Component({
  selector: 'app-alert',
  template: `
    @if (message()) {
      <div class="alert alert-{{ tone() }}" role="alert">
        <div style="min-width: 0">
          <div>
            <strong>{{ title() }}</strong>
            @if (code()) {
              <span class="badge badge-neutral" style="margin-left: 8px">{{
                code()
              }}</span>
            }
          </div>
          <div style="margin-top: 4px; white-space: pre-wrap">{{ message() }}</div>
          @if (detail()) {
            <pre>{{ detail() }}</pre>
          }
        </div>
      </div>
    }
  `,
})
export class Alert {
  readonly message = input<string | null>(null);
  readonly tone = input<Tone>('error');
  readonly title = input<string>('Error');
  readonly code = input<string>('');
  readonly detail = input<string | null>(null);
}

/** Extrae code/message/detail de un error, sea de dominio o no. */
export function describe(err: unknown): {
  code: string;
  message: string;
  detail: string | null;
} {
  if (err instanceof ApiRequestError) {
    return { code: err.code, message: err.message, detail: err.detail };
  }
  return {
    code: 'UNKNOWN',
    message: err instanceof Error ? err.message : String(err),
    detail: null,
  };
}
