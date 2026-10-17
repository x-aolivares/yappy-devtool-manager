import { Routes } from '@angular/router';

import { CompilePage } from './pages/compile/compile';
import { DeploymentsPage } from './pages/deployments/deployments';
import { HomePage } from './pages/home/home';
import { MigrateDataPage } from './pages/migrate-data/migrate-data';
import { ParamsDiffPage } from './pages/params-diff/params-diff';
import { ParamsReadPage } from './pages/params-read/params-read';
import { SchemaSyncPage } from './pages/schema-sync/schema-sync';
import { SessionDetailPage } from './pages/session-detail/session-detail';
import { SessionsPage } from './pages/sessions/sessions';
import { SqlPage } from './pages/sql/sql';
import { WorkflowDatabasePage } from './pages/workflow-database/workflow-database';
import { WorkflowKafkaPage } from './pages/workflow-kafka/workflow-kafka';
import { WorkflowServicesPage } from './pages/workflow-services/workflow-services';
import { WorkflowTunnelsPage } from './pages/workflow-tunnels/workflow-tunnels';

// `/params-diff` sí está, pero en modo de solo lectura: comparar dos ambientes es
// leer, y el comando para aplicar se copia desde la pantalla. Las páginas de
// escritura —`params-create`, `params-edit` y `db-diff`— se borraron del repo
// porque hoy no hay permiso para ejecutarlas.
// `/sessions` NO se sacó, a propósito — `params-read.ts` crea una sesión y tiene
// un link a esa vista, así que dejarla en 404 sería romper la página que sí se
// conserva.
export const routes: Routes = [
  { path: '', pathMatch: 'full', component: HomePage },
  {
    path: 'aws',
    children: [
      { path: 'params-read', component: ParamsReadPage },
      { path: 'params-diff', component: ParamsDiffPage },
    ],
  },
  {
    path: 'db',
    children: [
      { path: 'compile', component: CompilePage },
      { path: 'schema-sync', component: SchemaSyncPage },
      { path: 'migrate-data', component: MigrateDataPage },
      { path: 'sql', component: SqlPage },
    ],
  },
  // Workflow son cuatro páginas y no una con cuatro secciones. Leen del mismo
  // endpoint y cada una tiene su propio poll, así que abrirlas una por una es el
  // modo de uso: no se está mirando cuatro fotos del tracker a la vez.
  //
  // El orden es el de la pregunta que uno se hace cuando algo no levanta: primero
  // Kafka (que casi siempre es lo que se necesita para probar), después los
  // túneles, después los servicios propios y por último la base.
  { path: 'workflow/kafka', component: WorkflowKafkaPage },
  { path: 'workflow/tunnels', component: WorkflowTunnelsPage },
  { path: 'workflow/services', component: WorkflowServicesPage },
  { path: 'workflow/database', component: WorkflowDatabasePage },
  // El link viejo sigue funcionando: la pantalla era `/workflow/resources` y hay
  // marcadores y chats con esa URL. Kafka es donde caía la mitad de la pantalla.
  { path: 'workflow/resources', redirectTo: 'workflow/kafka', pathMatch: 'full' },
  { path: 'sessions', component: SessionsPage },
  { path: 'sessions/:sessionId', component: SessionDetailPage },
  { path: 'deployments', component: DeploymentsPage },
  { path: '**', redirectTo: '' },
];
