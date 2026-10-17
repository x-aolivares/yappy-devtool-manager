/**
 * Túneles de cluster: el catálogo de puertos y la tabla que los levanta.
 *
 * Es la única de las cuatro secciones que no es una grilla de tarjetas, y por eso
 * es la única que no usa `<app-resource-cards>`. El resto —el poll, el resumen, los
 * logs, los modales— viene igual de `WorkflowSectionPage`, y `sectionRows` son las
 * filas del catálogo: es lo que el resumen cuenta y lo que el `aria-live` anuncia.
 *
 * Dos cosas que esta pantalla tiene y las otras no:
 *
 * - **El cluster es un control de acá.** Kafka y Base de datos no dependen del
 *   cluster, así que el selector no les hace falta. Acá el `cluster` decide qué
 *   catálogo se edita y contra qué instancia se abre cada túnel.
 * - **El catálogo se escribe en disco.** Es `CLUSTER_PORTS` en `env.base`, no
 *   `localStorage` como en el mockup, así que el nombre se guarda con debounce
 *   —ver `writeLabel`.
 */
import { Component, computed, signal, viewChild } from '@angular/core';

import type { WorkflowPortInfo } from '../../api-gen/models';
import { BusyModal } from '../../components/busy-modal/busy-modal';
import { NoticeModal } from '../../components/notice-modal/notice-modal';
import { PageHeader } from '../../components/page-header/page-header';
import { WorkflowLogs } from '../../components/workflow-logs/workflow-logs';
import { WorkflowSummary } from '../../components/workflow-summary/workflow-summary';
import { toApiError } from '../../core/services/api-error';
import { WorkflowSectionPage } from '../../core/workflow-section-page';

/** Los clusters que el backend conoce. El host sale del estado, no de acá. */
const CLUSTERS = ['cap', 'cap2', 'int'] as const;

/** El guard de escritura del nombre del servicio. Ver `writeLabel`. */
const LABEL_DEBOUNCE_MS = 500;

@Component({
  selector: 'app-workflow-tunnels-page',
  imports: [BusyModal, NoticeModal, PageHeader, WorkflowLogs, WorkflowSummary],
  templateUrl: './workflow-tunnels.html',
  styleUrl: './workflow-tunnels.scss',
})
export class WorkflowTunnelsPage extends WorkflowSectionPage {
  /** Ver `WorkflowKafkaPage.logs`: las filas abren un panel declarado más abajo. */
  readonly logs = viewChild(WorkflowLogs);

  readonly clusters = CLUSTERS;

  readonly addPortValue = signal('');
  readonly addPortError = signal('');

  private readonly labelTimers = new Map<number, ReturnType<typeof setTimeout>>();

  /** El nombre que viaja en la URL de cada pedido. Ver `WorkflowSectionPage.section`. */
  readonly section = 'tunnels';

  /** El catálogo del cluster elegido. Son las filas de esta pantalla. */
  protected override get sectionRows(): readonly WorkflowPortInfo[] {
    return this.ports();
  }

  /** "3 puertos · 2 con nombre". Lo que el contador tiene que poder decir. */
  readonly catalogSummary = computed(() => {
    const rows = this.ports();
    const named = rows.filter((p) => p.label.trim()).length;
    return `${rows.length} ${rows.length === 1 ? 'puerto' : 'puertos'} · ${named} con nombre`;
  });

  /** Si hay alguna fila del catálogo que se pueda levantar de un click. */
  readonly hasDown = computed<boolean>(() =>
    this.ports().some((p) => p.state === 'down'),
  );

  constructor() {
    super();
    // Los timers del debounce son de esta pantalla y no de la base: el catálogo es
    // lo único que se edita en línea.
    this.destroyRef.onDestroy(() => {
      for (const timer of this.labelTimers.values()) clearTimeout(timer);
      this.labelTimers.clear();
    });
  }

  // --- catálogo -------------------------------------------------------------

  async addPort(): Promise<void> {
    const raw = this.addPortValue().trim();
    this.addPortError.set('');
    if (!raw) {
      this.addPortError.set('Falta el número de puerto.');
      return;
    }
    const port = Number(raw);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      this.addPortError.set('Tiene que ser un puerto entre 1 y 65535.');
      return;
    }
    try {
      await this.workflow.addPort(port, '', this.cluster());
      this.addPortValue.set('');
    } catch (err) {
      this.addPortError.set(toApiError(err).message);
    }
  }

  onAddPortKey(event: Event): void {
    if ((event as KeyboardEvent).key === 'Enter') {
      event.preventDefault();
      void this.addPort();
    }
  }

  /**
   * Levanta todas las filas que están abajo.
   *
   * Sólo `down`, a diferencia del botón de cada fila, que sí ofrece las caídas.
   * Un botón múltiple no tiene dónde decir "esto se cayó": apretarlo con tres
   * filas caídas y verlas volver a caerse no es lo que "Levantar todos" promete.
   * Para ésas está el botón de su fila, que sí lo dice.
   */
  async startAllDown(): Promise<void> {
    const pending = this.ports().filter((p) => p.state === 'down');
    if (!pending.length) return;
    this.busy.set(true);
    // Uno por uno y no en paralelo: cada túnel es un `ssm start-session` contra
    // la misma instancia, y lanzarlos de a cinco se pelean por la sesión.
    for (const row of pending) {
      try {
        await this.workflow.startPort(row.port, row.label, this.env(), this.cluster());
      } catch (err) {
        // Uno que falla no cancela los demás: el developer quiere los que puedan
        // y el que no pudo queda con su error de antes.
        this.showError('No se pudo levantar', toApiError(err).message);
        break;
      }
    }
    this.busy.set(false);
  }

  /**
   * El nombre del servicio se guarda solo, con debounce.
   *
   * El mockup guarda en cada tecla porque su catálogo está en `localStorage` y
   * una escritura a memoria no cuesta nada. Acá el catálogo es `CLUSTER_PORTS`
   * en `env.base`: guardar en cada tecla sería escribir un archivo treinta veces
   * por palabra. El debounce no es una diferencia de estilo, es no escribir el
   * archivo de un usuario treinta veces.
   */
  writeLabel(row: WorkflowPortInfo, value: string): void {
    const pending = this.labelTimers.get(row.port);
    if (pending) clearTimeout(pending);
    this.labelTimers.set(
      row.port,
      setTimeout(() => {
        this.labelTimers.delete(row.port);
        if (row.label === value.trim()) return;
        void this.workflow
          .renamePort(this.cluster(), row.port, value.trim())
          .catch((err) => this.showError('No se pudo guardar el nombre', toApiError(err).message));
      }, LABEL_DEBOUNCE_MS),
    );
  }

  /**
   * Quitar la fila.
   *
   * Primero para el túnel si está vivo: la fila que se borra es lo único que lo
   * puede parar. El backend igual lo rechaza — es la defensa del lado del
   * servidor, no un chequeo de acá.
   */
  async removePort(row: WorkflowPortInfo): Promise<void> {
    this.busy.set(true);
    try {
      if (row.state === 'up' || row.state === 'starting') {
        if (row.pid) await this.workflow.stop(row.pid);
      }
      await this.workflow.removePort(this.cluster(), row.port);
    } catch (err) {
      this.showError('No se pudo quitar el puerto', toApiError(err).message);
    } finally {
      this.busy.set(false);
    }
  }

  protected trackPort(_index: number, row: WorkflowPortInfo): number { return row.port; }
  protected trackUnattributed(_index: number, row: { pid: number }): number { return row.pid; }
}
