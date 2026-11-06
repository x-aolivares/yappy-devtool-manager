/**
 * Kafka local: el broker KRaft, Kafdrop y el Kafka Agent.
 *
 * Es la primera de las cuatro secciones de Workflow y la más chica: no tiene
 * catálogo, ni cluster, ni selector de ambiente. Las tarjetas son las de
 * `group: 'kafka'` menos el agente, que tiene su propio panel porque además de
 * levantarlo y pararlo hay que poder reiniciarlo.
 *
 * El agente sale del mismo stream que el resto de los recursos. Es un proceso del
 * tracker como Kafdrop, así que no hay un endpoint aparte para leer su estado.
 */
import { Component, computed, viewChild } from '@angular/core';

import type { WorkflowResourceInfo } from '../../api-gen/models';
import { BusyModal } from '../../components/busy-modal/busy-modal';
import { NoticeModal } from '../../components/notice-modal/notice-modal';
import { PageHeader } from '../../components/page-header/page-header';
import { ResourceCards } from '../../components/resource-cards/resource-cards';
import { WorkflowLogs } from '../../components/workflow-logs/workflow-logs';
import { WorkflowSummary } from '../../components/workflow-summary/workflow-summary';
import { toApiError } from '../../core/services/api-error';
import { WorkflowScope, WorkflowSectionPage } from '../../core/workflow-section-page';

/** La key con la que el registro del backend nombra al Kafka Agent. */
const AGENT_KEY = 'kafka-agent';

@Component({
  selector: 'app-workflow-kafka-page',
  imports: [
    BusyModal,
    NoticeModal,
    PageHeader,
    ResourceCards,
    WorkflowLogs,
    WorkflowSummary,
  ],
  templateUrl: './workflow-kafka.html',
  styles: [
    `
      .agent-status {
        display: flex;
        align-items: center;
        gap: 0.625rem;
        margin-bottom: 0.625rem;
      }
      .agent-help {
        margin: 0 0 0.875rem;
        color: var(--sys-text-secondary);
      }
    `,
  ],
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

  /** Dónde escucha el agente. Es el mismo puerto que el contenedor original. */
  readonly agentUrl = 'http://127.0.0.1:9091';

  /** Sólo Kafka. El `group` es el único discriminante que manda el backend. */
  protected override get sectionRows(): readonly WorkflowResourceInfo[] {
    return this.resources().filter((r) => r.group === 'kafka');
  }

  /**
   * Un ámbito vacío: esta sección no elige ambiente ni cluster, así que no los
   * pide. Kafka es full local, así que el poll sale sin `env` ni `cluster`.
   */
  protected override get scope(): WorkflowScope {
    return {};
  }

  /** Las tarjetas del broker y Kafdrop. El agente va en su propio panel. */
  readonly rows = computed(() => this.sectionRows.filter((r) => r.key !== AGENT_KEY));

  /** La fila del agente, o `null` mientras el stream no la trae. */
  readonly agentRow = computed<WorkflowResourceInfo | null>(
    () => this.sectionRows.find((r) => r.key === AGENT_KEY) ?? null,
  );

  readonly agentUp = computed(() => this.agentRow()?.state === 'up');

  startAgent(): Promise<void> {
    const row = this.agentRow();
    return row ? this.startResource(row) : Promise.resolve();
  }

  stopAgent(): Promise<void> {
    const row = this.agentRow();
    return row ? this.stopOne(row) : Promise.resolve();
  }

  /**
   * Parar y volver a levantar el proceso del agente.
   *
   * Es lo que hace falta después de reiniciar el broker: el agente abre sus
   * conexiones al primer uso, así que un proceso nuevo las abre contra el broker
   * que ya está arriba. Si el agente no estaba levantado, sólo se levanta.
   */
  async restartAgent(): Promise<void> {
    const row = this.agentRow();
    if (!row) return;
    this.busy.set(true);
    try {
      if (row.pid && row.state !== 'down') {
        await this.workflow.stop(row.pid);
      }
      await this.workflow.startResource(row.key);
    } catch (err) {
      this.showError('No se pudo reiniciar el Kafka Agent', toApiError(err).message);
    } finally {
      this.busy.set(false);
    }
  }
}
