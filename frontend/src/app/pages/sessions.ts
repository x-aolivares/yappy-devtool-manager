import { Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SessionSummaryInfo } from '../api-gen/models';
import { SessionService } from '../core/services/session.service';
import { toApiError } from '../core/services/api-error';
import { fmtDate } from '../core/format';
import { EmptyStateCardComponent } from '../shared/empty-state-card';
import { PageHeaderComponent } from '../shared/page-header';
import { PaginationBarComponent } from '../shared/pagination-bar';
import { paginate } from '../shared/paginate';
import { StatusBadge } from '../shared/status-badge';

@Component({
  selector: 'app-sessions-page',
  imports: [
    RouterLink,
    StatusBadge,
    PageHeaderComponent,
    EmptyStateCardComponent,
    PaginationBarComponent,
  ],
  templateUrl: './sessions.html',
})
export class SessionsPage {
  private readonly sessionService = inject(SessionService);

  readonly sessions = signal<SessionSummaryInfo[] | null>(null);
  readonly filterText = signal('');
  readonly error = signal<string | null>(null);
  readonly busy = signal(false);
  readonly deleting = signal(false);

  /**
   * La tabla de sesiones, paginada.
   *
   * El filtro va **antes** de paginar, no después: filtrar las 25 filas de la
   * página actual daría un resultado que depende de dónde estés parado, que es la
   * forma más confusa que tiene un filtro de romperse.
   */
  readonly filteredSessions = computed(() => {
    const q = this.filterText().trim().toLowerCase();
    const all = this.sessions() ?? [];
    if (!q) return all;
    return all.filter(
      (s) =>
        s.title.toLowerCase().includes(q) ||
        s.id.toLowerCase().includes(q) ||
        `${s.env_a} ${s.env_b}`.toLowerCase().includes(q),
    );
  });

  readonly page = paginate(() => this.filteredSessions());

  constructor() {
    void this.load();

    // Filtrar devuelve a la primera página: si no, buscar algo que matchea 2 de 40
    // con la grilla en la página 4 muestra una tabla vacía sin explicación.
    effect(() => {
      this.filterText();
      this.page.reset();
    });
  }

  load() {
    this.busy.set(true);
    this.error.set(null);
    return this.sessionService.list().then(
      (d) => {
        this.busy.set(false);
        this.sessions.set(d.sessions);
      },
      (err) => {
        this.busy.set(false);
        this.error.set(toApiError(err).message);
      },
    );
  }

  remove(id: string) {
    if (!confirm('¿Eliminar DEFINITIVAMENTE esta sesión y su progreso?\nNo se puede deshacer.')) return;
    this.deleting.set(true);
    this.sessionService.delete(id).then(
      () => {
        this.deleting.set(false);
        void this.load();
      },
      (err) => {
        this.deleting.set(false);
        this.error.set(toApiError(err).message);
      },
    );
  }

  protected readonly fmtDate = fmtDate;
}