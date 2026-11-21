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
import { Component, computed, signal, viewChild } from '@angular/core';

import type { WorkflowResourceInfo } from '../../api-gen/models';
import { BusyModal } from '../../components/busy-modal/busy-modal';
import { NoticeModal } from '../../components/notice-modal/notice-modal';
import { PageHeader } from '../../components/page-header/page-header';
import { ResourceCards } from '../../components/resource-cards/resource-cards';
import { WorkflowLogs } from '../../components/workflow-logs/workflow-logs';
import { WorkflowSummary } from '../../components/workflow-summary/workflow-summary';
import { copyText } from '../../core/copy';
import { WorkflowSectionPage } from '../../core/workflow-section-page';
import { toApiError } from '../../core/services/api-error';

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

  /** Mientras se genera el password. Un signal propio: no abre el modal de "Trabajando…". */
  readonly passwordBusy = signal(false);
  /** Prende el "Copiado" del botón un rato después de copiar. */
  readonly passwordCopied = signal(false);

  /**
   * Copia el password de la base del ambiente, como lo hace `yappy db up`.
   *
   * El valor sale del backend y se copia acá, en el navegador: el portapapeles
   * que importa es el de quien mira la pantalla. Si el copiado falla, el error va
   * al aviso y el password igual queda en `.env.local`.
   */
  async copyPassword(): Promise<void> {
    if (this.passwordBusy()) return;
    this.passwordBusy.set(true);
    try {
      const { password } = await this.workflow.dbPassword(this.env());
      await copyText(password);
      this.passwordCopied.set(true);
      setTimeout(() => this.passwordCopied.set(false), 1500);
    } catch (err) {
      this.showError('No se pudo copiar el password', toApiError(err).message);
    } finally {
      this.passwordBusy.set(false);
    }
  }
}
