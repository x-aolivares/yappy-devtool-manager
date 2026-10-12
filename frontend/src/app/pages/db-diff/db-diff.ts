import { Component, computed, inject, signal } from '@angular/core';
import { DiffResponse, EnvironmentInfo } from '../../api-gen/models';
import { EnvironmentService } from '../../core/services/environment.service';
import { DbService } from '../../core/services/db.service';
import { toApiError } from '../../core/services/api-error';
import { CancelSlot, isCancellation } from '../../core/cancel';
import { objectLabel } from '../../core/format';
import { BusyModal } from '../../components/busy-modal/busy-modal';
import { StatusBadge } from '../../components/status-badge';
import { CopyButton } from '../../components/copy-button/copy-button';
import { RegionControls } from '../../components/region-controls/region-controls';

@Component({
  selector: 'app-db-diff-page',
  imports: [RegionControls, StatusBadge, BusyModal, CopyButton],
  templateUrl: './db-diff.html',
})
export class DbDiffPage {
  private readonly envService = inject(EnvironmentService);
  private readonly dbService = inject(DbService);

  readonly environments = signal<EnvironmentInfo[] | null>(null);
  readonly envA = signal('');
  readonly envB = signal('');
  readonly schema = signal('');
  readonly objectName = signal('');
  readonly objectType = signal<'table' | 'procedure'>('table');
  readonly includeDeletes = signal(false);

  readonly busy = signal(false);
  readonly busyText = signal('');
  readonly error = signal<string | null>(null);
  readonly result = signal<DiffResponse | null>(null);

  /**
   * La comparación en vuelo, para que el botón del modal la corte.
   *
   * Sólo lee: lee el objeto en el origen y en el destino y arma el script. El
   * botón dice "Cancelar" y es literal —nada de lo que hace esta pantalla
   * escribe nada—.
   */
  private readonly op = new CancelSlot();

  constructor() {
    this.envService.list().then(
      (envs) => this.environments.set(envs.environments),
      (err) => this.error.set('No se pudieron cargar los ambientes: ' + toApiError(err).message),
    );
  }

  compare() {
    const schema = this.schema().trim();
    const objectName = this.objectName().trim();
    if (!this.envA() || !this.envB()) {
      this.error.set('Seleccioná las dos regiones.');
      return;
    }
    if (!schema || !objectName) {
      this.error.set('Completá el schema y el nombre del objeto.');
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.busyText.set(
      `Comparando ${objectLabel(this.objectType())} ${schema}.${objectName} de origen ${this.envB()} a destino ${this.envA()}...`,
    );

    const run = this.op.begin();
    this.dbService
      .diff(
        {
          env_a: this.envA(),
          env_b: this.envB(),
          schema_name: schema,
          object_type: this.objectType(),
          object_name: objectName,
          include_deletes: this.includeDeletes(),
        },
        run,
      )
      .then(
        (d) => {
          if (!this.op.finish(run)) return;
          this.busy.set(false);
          this.result.set(d);
        },
        (err) => {
          if (!this.op.finish(run)) return;
          this.busy.set(false);
          // Cortar la espera no es un fallo: el usuario lo pidió. Ver `core/cancel`.
          if (isCancellation(err)) return;
          this.error.set(toApiError(err).message);
        },
      );
  }

  /** Cortar la comparación en vuelo y dejar el diff anterior en pantalla. */
  cancelBusy(): void {
    if (!this.op.cancel()) return;
    this.busy.set(false);
  }

  pre(v: string | null | undefined, empty: string): string {
    return v ?? empty;
  }
}