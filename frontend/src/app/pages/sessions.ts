import { Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SessionSummaryInfo } from '../api-gen/models';
import { SessionService } from '../core/services/session.service';
import { toApiError } from '../core/services/api-error';
import { fmtDate } from '../core/format';
import { EmptyStateCard } from '../shared/empty-state-card';
import { NoticeModal } from '../shared/notice-modal';
import { PageHeader } from '../shared/page-header';
import { PaginationBar } from '../shared/pagination-bar';
import { paginate } from '../shared/paginate';
import { StatusBadge } from '../shared/status-badge';

@Component({
  selector: 'app-sessions-page',
  imports: [
    RouterLink,
    StatusBadge,
    PageHeader,
    EmptyStateCard,
    PaginationBar,
    NoticeModal,
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
   * La confirmación pendiente de borrar una sesión.
   *
   * Es un signal y no un `confirm()` porque la respuesta del usuario llega
   * después: el modal es asíncrono. El click guarda qué borrar, el modal
   * pregunta, y `borrar()` corre sólo si confirmó.
   *
   * El texto va partido en párrafos porque el cuerpo del modal es HTML y un
   * `\n` adentro de un `<p>` es un espacio. Ver `styles.scss`,
   * `.notice-modal__body p`.
   */
  readonly pending = signal<{ titulo: string; parrafos: string[] } | null>(null);

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

  /** El click en "Eliminar": abre el modal y guarda qué borrar. */
  remove(id: string) {
    this.pending.set({
      titulo: 'Eliminar sesión',
      parrafos: [
        'Se va a eliminar DEFINITIVAMENTE la sesión y todo su progreso.',
        'No se puede deshacer.',
      ],
    });
    this.pendingId = id;
  }

  /** Confirmó en el modal: recién acá se borra. */
  borrar(): void {
    const id = this.pendingId;
    this.cancelar();
    if (id === null) return;
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

  /** ✕, Escape, backdrop o "Cancelar": no se borra nada. */
  cancelar(): void {
    this.pending.set(null);
    this.pendingId = null;
  }

  private pendingId: string | null = null;

  protected readonly fmtDate = fmtDate;
}