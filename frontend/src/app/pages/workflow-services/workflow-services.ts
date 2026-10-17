/**
 * Servicios con destino propio: los que no dependen del cluster.
 *
 * La diferencia con Kafka y con Base de datos es una sola y está en el puerto: acá
 * el recurso traduce uno en otro, así que la tarjeta muestra
 * `:remoto → :local` y callar el puerto de destino esconde información. Por eso el
 * `portMode` de `<app-resource-cards>` existe y no es un detalle de maquetado.
 *
 * La otra diferencia es el aviso del pie: `yappy stop tunnel` mata todas las
 * sesiones SSM, incluidas las de otro proyecto. Por eso las filas ajenas van con
 * borde punteado y la etiqueta *ajeno* —que las dibuja el componente compartido—.
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
  selector: 'app-workflow-services-page',
  imports: [
    BusyModal,
    NoticeModal,
    PageHeader,
    ResourceCards,
    WorkflowLogs,
    WorkflowSummary,
  ],
  templateUrl: './workflow-services.html',
})
export class WorkflowServicesPage extends WorkflowSectionPage {
  /** Ver `WorkflowKafkaPage.logs`: las filas abren un panel declarado más abajo. */
  readonly logs = viewChild(WorkflowLogs);

  /** El nombre que viaja en la URL de cada pedido. Ver `WorkflowSectionPage.section`. */
  readonly section = 'services';

  protected override get sectionRows(): readonly WorkflowResourceInfo[] {
    return this.resources().filter((r) => r.group === 'named');
  }

  readonly rows = computed(() => this.sectionRows);
}
