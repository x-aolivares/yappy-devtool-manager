import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SessionDetailResponse, SessionItemInfo } from '../api-gen/models';
import { SessionService } from '../core/services/session.service';
import { toApiError } from '../core/services/api-error';
import { fmtDate } from '../core/format';
import { PageHeader } from '../shared/page-header';
import { PaginationBar } from '../shared/pagination-bar';
import { paginate } from '../shared/paginate';
import { StatusBadge } from '../shared/status-badge';

const STATUS_META: Record<string, [string, string]> = {
  pendiente: ['none', 'Pendiente'],
  revisado: ['equal', 'Revisado'],
  aplicado: ['ok', 'Aplicado'],
  saltado: ['missing_in_b', 'Saltado'],
};

@Component({
  selector: 'app-session-detail-page',
  imports: [RouterLink, StatusBadge, PageHeader, PaginationBar],
  templateUrl: './session-detail.html',
})
export class SessionDetailPage {
  private readonly sessionService = inject(SessionService);

  readonly sessionId = input.required<string>();

  readonly session = signal<SessionDetailResponse | null>(null);
  readonly busy = signal(false);
  readonly actBusy = signal(false);
  readonly error = signal<string | null>(null);
  readonly filterText = signal('');
  readonly newItemName = signal('');

  constructor() {
    effect(() => {
      const id = this.sessionId();
      if (id) void this.load(id);
    });

    // Filtrar devuelve a la primera página: con la grilla en la página 4, un
    // filtro que deja 3 filas mostraría una tabla vacía sin explicación.
    effect(() => {
      this.filterText();
      this.page.reset();
    });
  }

  load(id: string) {
    this.busy.set(true);
    this.error.set(null);
    return this.sessionService.get(id).then(
      (d) => {
        this.busy.set(false);
        this.session.set(d);
      },
      (err) => {
        this.busy.set(false);
        this.error.set(toApiError(err).message);
      },
    );
  }

  setStatus(name: string, status: string) {
    const id = this.sessionId();
    if (!id) return;
    this.actBusy.set(true);
    this.error.set(null);
    this.sessionService.updateItem(id, { name, status }).then(
      () => {
        this.actBusy.set(false);
        void this.load(id);
      },
      (err) => {
        this.actBusy.set(false);
        this.error.set(toApiError(err).message);
      },
    );
  }

  doneCount(session: SessionDetailResponse): number {
    if (!session.status_counts) return 0;
    return (session.status_counts['aplicado'] || 0) + (session.status_counts['saltado'] || 0);
  }

  donePct(session: SessionDetailResponse): number {
    const total = session.items.length;
    return total ? Math.round((this.doneCount(session) / total) * 100) : 0;
  }

  pendingCount(session: SessionDetailResponse): number {
    return session.items.filter((i) => i.status === 'pendiente').length;
  }

  filteredItems(): SessionItemInfo[] {
    const session = this.session();
    if (!session) return [];
    const q = this.filterText().trim().toLowerCase();
    if (!q) return session.items;
    return session.items.filter((item) => item.name.toLowerCase().includes(q));
  }

  /**
   * Los ítems de la sesión, filtrados y paginados.
   *
   * El filtro va antes de paginar, por el mismo motivo que en la lista de
   * sesiones: filtrar sólo las filas de la página actual daría un resultado que
   * depende de dónde estés parado. Y el `position` de cada ítem es su número real
   * en la sesión, así que paginar no renumera nada — la columna `#` sigue diciendo
   * "el ítem 47 de 300" y no "el ítem 22 de 25".
   */
  readonly page = paginate(() => this.filteredItems());

  addItem() {
    const id = this.sessionId();
    const name = this.newItemName().trim();
    if (!id || !name) return;
    this.actBusy.set(true);
    this.error.set(null);
    this.sessionService
      .createItem(id, { name, status: 'pendiente', service: this.session()?.service || 'ssm' })
      .then(
        () => {
          this.actBusy.set(false);
          this.newItemName.set('');
          void this.load(id);
        },
        (err) => {
          this.actBusy.set(false);
          this.error.set(toApiError(err).message);
        },
      );
  }

  nextItem(session: SessionDetailResponse): SessionItemInfo | null {
    return session.items.find((i) => i.status === 'pendiente') ?? null;
  }

  diffQuery(item: SessionItemInfo, session: SessionDetailResponse): Record<string, string> {
    return {
      session: session.id,
      env_a: session.env_a,
      env_b: session.env_b,
      service: item.service || session.service || 'ssm',
      name: item.name,
      with_secret: item.is_secret ? '1' : '0',
    };
  }

  statusMeta(status: string): [string, string] {
    return STATUS_META[status] ?? ['none', status];
  }

  itemDetailsText(item: SessionItemInfo): string {
    const parts: string[] = [];
    if (item.diff_err) parts.push(`Error al diferir:\n${item.diff_err}`);
    if (item.notes) parts.push(item.notes);
    if (item.script) parts.push(`Comando:\n${item.script}`);
    if (item.preview) parts.push(`Valor a aplicar:\n${item.preview}`);
    return parts.join('\n\n');
  }

  reportUrl(): string {
    const id = this.sessionId();
    return id ? `/api/sessions/${encodeURIComponent(id)}/report.md` : '#';
  }

  protected readonly fmtDate = fmtDate;
}