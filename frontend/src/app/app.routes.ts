import { Routes } from '@angular/router';

import { CompilePage } from './pages/compile';
import { DeploymentsPage } from './pages/deployments';
import { HomePage } from './pages/home';
import { MigrateDataPage } from './pages/migrate-data';
import { ParamsReadPage } from './pages/params-read';
import { SchemaSyncPage } from './pages/schema-sync';
import { SessionDetailPage } from './pages/session-detail';
import { SessionsPage } from './pages/sessions';
import { SqlPage } from './pages/sql';

// `/params-diff`, `/params-create`, `/params-edit` y `/db-diff` dejaron de estar
// enrutadas: sus páginas siguen en el repo, pero ya no se llega a ellas por URL.
// `/sessions` NO se sacó, a propósito — `params-read.ts` crea una sesión y tiene
// un link a esa vista, así que dejarla en 404 sería romper la página que sí se
// conserva.
export const routes: Routes = [
  { path: '', pathMatch: 'full', component: HomePage },
  { path: 'params-read', component: ParamsReadPage },
  { path: 'sessions', component: SessionsPage },
  { path: 'sessions/:sessionId', component: SessionDetailPage },
  { path: 'compile', component: CompilePage },
  { path: 'schema-sync', component: SchemaSyncPage },
  { path: 'migrate-data', component: MigrateDataPage },
  { path: 'sql', component: SqlPage },
  { path: 'deployments', component: DeploymentsPage },
  { path: '**', redirectTo: '' },
];
