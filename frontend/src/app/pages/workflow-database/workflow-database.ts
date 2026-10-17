/**
 * Base de datos: el túnel a Aurora.
 *
 * Es la sección más chica de las cuatro —el backend manda una sola fila de
 * `group: 'db'`— pero tiene su propia página igual que las otras, y no por
 * simetría: es la que se mira cuando la base no levanta y el log es lo primero que
 * se quiere ver. Escondida debajo de Kafka obligaba a buscarla.
 *
 * No lleva `portMode="remote"`: un túnel a Aurora no traduce un puerto, expone el
 * mismo en los dos extremos.
 */
import { Component, computed, viewChild } from '@angular/core';

import type { WorkflowResourceInfo } from '../../api-gen/models';
import { BusyModal } from '../../components/busy-modal/busy-modal';
import { NoticeModal } from '../../components/notice-modal/notice-modal';
import { PageHeader } from '../../components/page-header/page-header';
import { ResourceCards } from '../../components/resource-cards/resource-cards';
import { WorkflowLogs } from '../../components/workflow-logs/workflow-logs';
import { WorkflowSummary } from '../../components/workflow-summary/workflow-summary';
import { WorkflowSectionPage } from '../../core/workflow-section-page';

@Component({
  selector: 'app-workflow-database-page',
  imports: [
    BusyModal,
    NoticeModal,
    PageHeader,
    ResourceCards,
    WorkflowLogs,
    WorkflowSummary,
  ],
  templateUrl: './workflow-database.html',
})
export class WorkflowDatabasePage extends WorkflowSectionPage {
  /** Ver `WorkflowKafkaPage.logs`: las filas abren un panel declarado más abajo. */
  readonly logs = viewChild(WorkflowLogs);

  /** El nombre que viaja en la URL de cada pedido. Ver `WorkflowSectionPage.section`. */
  readonly section = 'database';

  protected override get sectionRows(): readonly WorkflowResourceInfo[] {
    return this.resources().filter((r) => r.group === 'db');
  }

  readonly rows = computed(() => this.sectionRows);
}
