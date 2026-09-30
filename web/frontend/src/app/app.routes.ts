import { Routes } from '@angular/router';

/**
 * Rutas de la app.
 *
 * `loadComponent` diferido: el shell y el core se descargan siempre, pero cada
 * feature entra solo cuando se la visita. Con cuatro features la diferencia es
 * marginal hoy, pero evita arrastrar el editor de SQL y el explorador de DDL en
 * la primera paint de Inicio.
 *
 * Los errores de carga de una route se reportan en el router en vez de dejar la
 * Outlet vacía: un chunk que no baja tiene que ser visible.
 */
export const routes: Routes = [
  { path: '', pathMatch: 'full', loadComponent: () => import('./features/home/home').then((m) => m.Home) },
  {
    path: 'parameters',
    loadComponent: () => import('./features/parameters/parameters').then((m) => m.Parameters),
  },
  {
    path: 'databases',
    loadComponent: () => import('./features/databases/databases').then((m) => m.Databases),
  },
  {
    path: 'query',
    loadComponent: () => import('./features/query/query').then((m) => m.Query),
  },
  { path: '**', redirectTo: '' },
];
