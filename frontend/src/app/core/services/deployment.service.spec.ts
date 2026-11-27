/**
 * La copia local del catálogo de repositorios.
 *
 * No decide nada —el TTL y el refresh son del backend—, pero tiene que
 * sobrevivir a un backend caído: es lo que pinta las sugerencias de la próxima
 * carga sin esperar ni fallar. Por eso lo que se guarda tiene que ser la misma
 * forma que devuelve la API, y lo que no la tenga cuenta como "no hay copia".
 */
import { TestBed } from '@angular/core/testing';
import { Api } from '../../api-gen/api';
import type { DeploymentReposResponse } from '../../api-gen/models';
import { DeploymentService } from './deployment.service';

const CATALOG: DeploymentReposResponse = {
  workspace: 'bg-ti',
  repositories: ['yappy-a', 'yappy-b'],
  fetched_at: '2026-11-27T10:00:00',
  stale: false,
};

const KEY = 'yappy.deployment-repos.v1';

describe('DeploymentService', () => {
  let respuesta: DeploymentReposResponse | null;

  beforeEach(() => {
    localStorage.clear();
    respuesta = CATALOG;
    TestBed.configureTestingModule({
      providers: [{ provide: Api, useValue: { invoke: () => Promise.resolve(respuesta) } }],
    });
  });

  it('la copia queda guardada después de consultar', async () => {
    const service = TestBed.inject(DeploymentService);

    await service.repos();

    expect(service.cachedRepos()).toEqual(CATALOG);
  });

  it('un refresh también deja la copia, que es lo que sirve de respaldo', async () => {
    const service = TestBed.inject(DeploymentService);

    await service.repos(true);

    expect(service.cachedRepos()).toEqual(CATALOG);
  });

  it('sin nada guardado no hay copia', () => {
    expect(TestBed.inject(DeploymentService).cachedRepos()).toBeNull();
  });

  it('una copia ilegible cuenta como que no hay', () => {
    // No es un error que valga romper la pantalla: se vuelve a consultar.
    localStorage.setItem(KEY, 'esto no es json');

    expect(TestBed.inject(DeploymentService).cachedRepos()).toBeNull();
  });

  it('una copia con otra forma cuenta como que no hay', () => {
    localStorage.setItem(KEY, JSON.stringify({ repositories: 'no es una lista' }));

    expect(TestBed.inject(DeploymentService).cachedRepos()).toBeNull();
  });

  it('si la consulta falla, la copia anterior sigue ahí', async () => {
    const service = TestBed.inject(DeploymentService);
    await service.repos();
    respuesta = null;
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        { provide: Api, useValue: { invoke: () => Promise.reject(new Error('backend caído')) } },
      ],
    });
    const conFallo = TestBed.inject(DeploymentService);

    await expect(conFallo.repos()).rejects.toThrow('backend caído');

    expect(conFallo.cachedRepos()).toEqual(CATALOG);
  });
});
