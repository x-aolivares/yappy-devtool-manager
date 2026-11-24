/**
 * Kafka local: el broker KRaft y Kafdrop, y nada más.
 *
 * Es la primera de las cuatro secciones de Workflow y la más chica: no tiene
 * catálogo, ni cluster, ni selector de ambiente, sólo las tarjetas de
 * `group: 'kafka'` y todo lo que las cuatro comparten —el poll, el resumen, los
 * logs y los modales—, que viene de `WorkflowSectionPage`.
 *
 * Lo único que esta página dice de sus filas es cuáles son: `sectionRows`. Esa
 * una línea es la que decide qué cuenta el resumen y qué se anuncia en el
 * `aria-live`, así que no se puede cambiar sin que las otras tres cambien.
 *
 * Y lo único que dice de sí misma es que no tiene ámbito: `scope` vacío. Ver abajo.
 */
import { Component, computed, viewChild } from '@angular/core';

import type { WorkflowResourceInfo } from '../../api-gen/models';
import { BusyModal } from '../../components/busy-modal/busy-modal';
import { KafkaAgentPanel } from '../../components/kafka-agent-panel/kafka-agent-panel';
import { NoticeModal } from '../../components/notice-modal/notice-modal';
import { PageHeader } from '../../components/page-header/page-header';
import { ResourceCards } from '../../components/resource-cards/resource-cards';
import { WorkflowLogs } from '../../components/workflow-logs/workflow-logs';
import { WorkflowSummary } from '../../components/workflow-summary/workflow-summary';
import { toApiError } from '../../core/services/api-error';
import { WorkflowScope, WorkflowSectionPage } from '../../core/workflow-section-page';

@Component({
  selector: 'app-workflow-kafka-page',
  imports: [
    BusyModal,
    KafkaAgentPanel,
    NoticeModal,
    PageHeader,
    ResourceCards,
    WorkflowLogs,
    WorkflowSummary,
  ],
  templateUrl: './workflow-kafka.html',
})
export class WorkflowKafkaPage extends WorkflowSectionPage {
  /**
   * El panel de logs, por referencia.
   *
   * Se usa con `viewChild` y no con una variable de template (`#logs`) porque las
   * filas que lo abren están **arriba** y una `#logs` sólo existe a partir de la
   * línea donde se declara.
   */
  readonly logs = viewChild(WorkflowLogs);

  /** El nombre que viaja en la URL de cada pedido. Ver `WorkflowSectionPage.section`. */
  readonly section = 'kafka';

  /** Sólo Kafka. El `group` es el único discriminante que manda el backend. */
  protected override get sectionRows(): readonly WorkflowResourceInfo[] {
    return this.resources().filter((r) => r.group === 'kafka');
  }

  /**
   * Un ámbito vacío: esta sección no elige ambiente ni cluster, así que no los
   * pide.
   *
   * Kafka es full local —el backend lo levanta con `Config()`, sin env— así que
   * `env=dev&cluster=cap` en cada poll de 1,5s eran dos query params fijos: la
   * respuesta es idéntica sin ellos y el log del server deja de llenarse de
   * líneas que nadie cambia. Tampoco se pide la lista de ambientes, porque acá no
   * hay un selector que la muestre.
   *
   * Servicios, Base de datos y Túneles sí dependen del ámbito y siguen con el de
   * la base.
   */
  protected override get scope(): WorkflowScope {
    return {};
  }

  readonly rows = computed(() => this.sectionRows);

  /** La fila del agente: la que el panel usa para saber si el proceso está arriba. */
  readonly agentRow = computed<WorkflowResourceInfo | null>(
    () => this.resources().find((row) => row.key === 'kafka-agent') ?? null,
  );

  /**
   * Start y stop reutilizan las acciones de la base: el agente es un recurso del
   * tracker como cualquier otro y no merece un segundo camino al backend.
   */
  protected startAgent(): void {
    const row = this.agentRow() ?? ({ key: 'kafka-agent' } as WorkflowResourceInfo);
    void this.startResource(row);
  }

  protected stopAgent(): void {
    const row = this.agentRow();
    if (row) void this.stopOne(row);
  }

  /**
   * Reiniciar es parar y levantar, con el `busy` puesto durante los dos pasos.
   * El pid se guarda antes del stop: el stream todavía no actualizó la fila cuando
   * llega el momento de levantar de nuevo.
   */
  protected async restartAgent(): Promise<void> {
    const row = this.agentRow();
    if (!row?.pid) return;
    this.busy.set(true);
    try {
      await this.workflow.stop(row.pid);
      await this.workflow.startResource(row.key);
    } catch (err) {
      this.showError('No se pudo reiniciar el agente', toApiError(err).message);
    } finally {
      this.busy.set(false);
    }
  }
}
