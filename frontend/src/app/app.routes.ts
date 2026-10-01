import { Routes } from '@angular/router';

import { CompilePage } from './pages/compile';
import { DbDiffPage } from './pages/db-diff';
import { HomePage } from './pages/home';
import { ParamsCreatePage } from './pages/params-create';
import { ParamsDiffPage } from './pages/params-diff';
import { ParamsEditPage } from './pages/params-edit';
import { ParamsReadPage } from './pages/params-read';
import { SessionDetailPage } from './pages/session-detail';
import { SessionsPage } from './pages/sessions';
import { SqlPage } from './pages/sql';

export const routes: Routes = [
  { path: '', pathMatch: 'full', component: HomePage },
  { path: 'params-read', component: ParamsReadPage },
  { path: 'sessions', component: SessionsPage },
  { path: 'sessions/:sessionId', component: SessionDetailPage },
  { path: 'params-create', component: ParamsCreatePage },
  { path: 'params-diff', component: ParamsDiffPage },
  { path: 'params-edit', component: ParamsEditPage },
  { path: 'db-diff', component: DbDiffPage },
  { path: 'compile', component: CompilePage },
  { path: 'sql', component: SqlPage },
  { path: '**', redirectTo: '' },
];