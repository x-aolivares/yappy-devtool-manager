/**
 * La vista de "Rama desplegada".
 *
 * La tabla es lo importante y su honestidad más que su forma: una fila que no
 * pudo completarse tiene que seguir mostrando lo que sí se resolvió (familia,
 * revisión, pipeline) en vez de volverse una celda en blanco. Un "—" donde
 * debería haber un dato hace creer que el ambiente no tiene despliegue.
 */
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { DeploymentsPage } from './deployments';
import { DeploymentService } from '../../core/services/deployment.service';
import { EnvironmentService } from '../../core/services/environment.service';
import type { DeploymentsBranchesResponse, DeploymentReposResponse } from '../../api-gen/models';

/** El catálogo de repos, con uno que sólo se encuentra por el medio del nombre. */
const CATALOG: DeploymentReposResponse = {
  workspace: 'bg-ti',
  repositories: [
    'yappy-trnxd-backend-payment-aggregator',
    'yappy-trnxd-backend-refund-service',
    'yappy-trnxd-frontend-web',
  ],
  fetched_at: '2026-11-27T10:00:00',
  stale: false,
};

const OK: DeploymentsBranchesResponse = {
  repo: 'yappy-trnxd-backend-payment-aggregator',
  group_name: 'trnxd',
  service_name: 'payment-aggregator',
  cluster: 'capabilities',
  task_definition_type: 'backend',
  environments: [
    {
      env: 'qa',
      region: 'us-west-1',
      status: 'ok',
      task_definition_family: 'yappy-trnxd-payment-aggregator-qa-family',
      task_definition_revision: 39,
      image: 'ecr/payment-aggregator:build.210',
      pipeline_id: 210,
      branch: 'feature/xyz',
      commit: 'abc1234',
      pipeline_url: 'https://app.circleci.com/pipelines/bb/bg-ti/repo/210',
      message: null,
    },
    {
      env: 'prod',
      region: 'us-west-1',
      status: 'no_task_definition',
      task_definition_family: 'yappy-trnxd-payment-aggregator-prod-family',
      task_definition_revision: null,
      image: null,
      pipeline_id: null,
      branch: null,
      commit: null,
      pipeline_url: null,
      message: "ECS no tiene la familia 'yappy-trnxd-payment-aggregator-prod-family'.",
    },
  ],
};

const OK_WITH_SUBJECT: DeploymentsBranchesResponse = {
  ...OK,
  environments: [
    {
      ...OK.environments[0],
      commit: 'ca67134f650e362133e51a9ffdb8e5ddc7fa53a5',
      commit_subject: 'fix: una cosa puntual',
    },
    OK.environments[1],
  ],
};

describe('DeploymentsPage', () => {
  let asked: string[];
  let askedEnvs: (string[] | undefined)[];
  let answer: (repo: string) => Promise<DeploymentsBranchesResponse>;
  /** Si el backend se llamó con `refresh`, que es el botón "Actualizar". */
  let reposAsked: boolean[];
  let reposAnswer: () => Promise<DeploymentReposResponse>;
  /** La copia de `localStorage`, que la página lee antes de tocar el backend. */
  let cachedCatalog: DeploymentReposResponse | null;

  const ENVS = [
    { env: 'dev', region: 'us-west-2', profile: 'base-profile' },
    { env: 'qa', region: 'us-west-1', profile: 'base-profile' },
    { env: 'uat', region: 'us-east-2', profile: 'base-profile' },
    // Un ambiente local, que la lista tiene que filtrar: esto pregunta por lo
    // desplegado en ECS y `local` no tiene nada desplegado.
    { env: 'local', is_local: true },
  ];

  beforeEach(async () => {
    asked = [];
    askedEnvs = [];
    answer = () => Promise.resolve(OK);
    reposAsked = [];
    reposAnswer = () => Promise.resolve(CATALOG);
    cachedCatalog = null;
    await TestBed.configureTestingModule({
      imports: [DeploymentsPage],
      providers: [
        {
          provide: DeploymentService,
          useValue: {
            branches: (repo: string, envs?: string[]) => {
              asked.push(repo);
              askedEnvs.push(envs);
              return answer(repo);
            },
            repos: (refresh = false) => {
              reposAsked.push(refresh);
              return reposAnswer();
            },
            cachedRepos: () => cachedCatalog,
          },
        },
        {
          provide: EnvironmentService,
          useValue: { list: () => Promise.resolve({ environments: ENVS }) },
        },
      ],
    }).compileComponents();
  });

  async function settle(fixture: ComponentFixture<DeploymentsPage>): Promise<void> {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  }

  async function consultar(
    repo: string,
  ): Promise<{ comp: any; el: HTMLElement; fixture: ComponentFixture<DeploymentsPage> }> {
    const fixture = TestBed.createComponent(DeploymentsPage);
    const comp = fixture.componentInstance as any;
    comp.repo.set(repo);
    comp.search();
    await settle(fixture);
    return { comp, el: fixture.nativeElement as HTMLElement, fixture };
  }

  it('sin marcar nada consulta todos los ambientes', async () => {
    // Es la pregunta habitual —"¿qué hay desplegado?"— y obligar a marcar cuatro
    // píldoras para verla sería una barrera sin motivo.
    await consultar('repo');

    expect(askedEnvs[0]).toBeUndefined();
  });

  it('la lista de ambientes sale de la config, sin nada hardcodeado', async () => {
    const { el } = await consultar('repo');

    const botones = el.querySelectorAll('app-env-picker .env-item');
    expect(botones.length).toBe(3);
    expect(el.querySelector('app-env-picker')!.textContent).toContain('uat');
  });

  it('con un ambiente marcado consulta sólo ese', async () => {
    // El caso que motivó el selector: mirar una región sin pagar las otras.
    const { comp, fixture } = await consultar('repo');
    comp.envs.set(['uat']);
    comp.search();
    await settle(fixture);

    expect(askedEnvs[askedEnvs.length - 1]).toEqual(['uat']);
  });

  it('con dos marcados consulta sólo esos dos', async () => {
    const { comp, fixture } = await consultar('repo');
    comp.envs.set(['uat', 'qa']);
    comp.search();
    await settle(fixture);

    expect(askedEnvs[askedEnvs.length - 1]).toEqual(['uat', 'qa']);
  });

  it('desmarcar todos vuelve a consultar todos', async () => {
    // Vacío y "todos" son lo mismo: es lo que evita que un clic de más deje
    // la pantalla en cero filas sin querer.
    const { comp, fixture } = await consultar('repo');
    comp.envs.set(['uat']);
    comp.toggleAll();
    comp.search();
    await settle(fixture);

    expect(askedEnvs[askedEnvs.length - 1]).toBeUndefined();
  });

  it('el botón de todos marca todo y después lo desmarca', async () => {
    const { comp, el, fixture } = await consultar('repo');

    comp.toggleAll();
    await settle(fixture);
    expect(comp.envs()).toEqual(['dev', 'qa', 'uat']);

    const boton = el.querySelector<HTMLButtonElement>('.field-label-row button')!;
    expect(boton.textContent).toContain('Ninguno');
    boton.click();
    await settle(fixture);

    expect(comp.envs()).toEqual([]);
  });

  it('no ofrece los ambientes locales', async () => {
    // `local` es un ambiente de *base de datos*: se alcanza por TCP contra el
    // MySQL de la máquina. Esto pregunta por lo que está desplegado en ECS, así
    // que ofrecerlo es ofrecer una consulta que no puede responder.
    const { comp } = await consultar('repo');

    expect((comp.environments() as { env: string }[]).map((e) => e.env)).toEqual([
      'dev',
      'qa',
      'uat',
    ]);
  });

  it('no manda un ambiente que no está en la lista', async () => {
    // Si el picker todavía no cargó, un `envs` con todo sería una lista inventada.
    const { comp, fixture } = await consultar('repo');
    comp.environments.set([]);
    comp.envs.set(['inventado']);
    comp.search();
    await settle(fixture);

    expect(askedEnvs[askedEnvs.length - 1]).toBeUndefined();
  });

  it('el texto de ayuda cuenta cuántos se van a consultar', async () => {
    const { comp, el, fixture } = await consultar('repo');

    // Sin marcar no es "0 de 3": es todos, y el texto tiene que decirlo distinto.
    expect(el.querySelector('.hint:not(.repos-hint)')!.textContent).toContain('todos');

    comp.envs.set(['uat']);
    await settle(fixture);
    expect(el.querySelector('.hint:not(.repos-hint)')!.textContent).toContain('1 de 3');

    comp.envs.set(['uat', 'qa']);
    await settle(fixture);
    expect(el.querySelector('.hint:not(.repos-hint)')!.textContent).toContain('2 de 3');
  });

  it('consulta el repo que se escribió, sin el workspace', async () => {
    await consultar('yappy-trnxd-backend-payment-aggregator');

    expect(asked).toEqual(['yappy-trnxd-backend-payment-aggregator']);
  });

  it('pide un ambiente por fila', async () => {
    const { el } = await consultar('repo');

    const filas = el.querySelectorAll('tbody tr');
    expect(filas.length).toBe(2);
    expect(filas[0].textContent).toContain('QA');
    expect(filas[0].textContent).toContain('feature/xyz');
    expect(filas[1].textContent).toContain('PROD');
  });

  it('sin repo no pregunta nada y lo explica en un modal', async () => {
    const fixture = TestBed.createComponent(DeploymentsPage);
    const comp = fixture.componentInstance as any;
    comp.repo.set('   ');
    comp.search();
    await settle(fixture);

    const el = fixture.nativeElement as HTMLElement;
    expect(asked).toEqual([]);
    expect(el.querySelector('app-notice-modal')).not.toBeNull();
    expect(el.querySelector('.notice-modal')!.textContent).toContain('Falta el repositorio');
  });

  it('una fila que no se completó conserva lo que sí se resolvió', async () => {
    answer = () =>
      Promise.resolve({
        ...OK,
        environments: [
          {
            ...OK.environments[0],
            status: 'circleci_unavailable',
            branch: null,
            commit: null,
            message: 'Falta CIRCLECI_TOKEN en la config.',
          },
        ],
      });

    const { el } = await consultar('repo');

    const fila = el.querySelector('tbody tr')!;
    // El motivo del corte…
    expect(fila.textContent).toContain('CIRCLECI_TOKEN');
    // …y lo que ya estaba resuelto sigue en la fila, que es lo comparable.
    expect(fila.textContent).toContain('210');
    expect(fila.textContent).toContain('39');
  });

  it('si la consulta falla, la tabla anterior no queda', async () => {
    const { comp, el, fixture } = await consultar('repo');
    expect(el.querySelector('tbody')).not.toBeNull();

    answer = () => Promise.reject(new HttpErrorResponse({ status: 502, error: {} }));
    comp.search();
    await settle(fixture);

    expect(el.querySelector('tbody')).toBeNull();
    expect(comp.result()).toBeNull();
  });

  it('un 405 se explica como backend viejo, no como "Method Not Allowed"', async () => {
    answer = () =>
      Promise.reject(
        new HttpErrorResponse({ status: 405, error: { detail: 'Method Not Allowed' } }),
      );

    const { el } = await consultar('repo');

    const modal = el.querySelector('.notice-modal')!.textContent ?? '';
    expect(modal).toContain('todavía');
    expect(modal).toContain('yappy web --watch');
  });

  it('una credencial faltante muestra cuáles y dónde ponerlas', async () => {
    answer = () =>
      Promise.reject(
        new HttpErrorResponse({
          status: 502,
          error: { detail: 'Falta BITBUCKET_TOKEN en la config' },
        }),
      );

    const { el } = await consultar('repo');

    const modal = el.querySelector('.notice-modal')!.textContent ?? '';
    expect(modal).toContain('BITBUCKET_TOKEN');
    expect(modal).toContain('backend/config/env.base');
    // Y algo para pegar sin tipear el nombre de la variable.
    expect(el.querySelector('.notice-snippet')!.textContent).toContain('BITBUCKET_TOKEN=');
  });

  it('el modal se cierra y la tabla no vuelve hasta consultar de nuevo', async () => {
    answer = () => Promise.reject(new HttpErrorResponse({ status: 502, error: {} }));
    const { comp, el, fixture } = await consultar('repo');
    expect(el.querySelector('.notice-modal')).not.toBeNull();

    comp.failure.set(null);
    await settle(fixture);

    expect(el.querySelector('.notice-modal')).toBeNull();
    // Sin tabla: la consulta falló.
    expect(el.querySelector('tbody')).toBeNull();
  });

  it('una fila sin rama ofrece el arreglo del token de CircleCI', async () => {
    answer = () =>
      Promise.resolve({
        ...OK,
        environments: [
          {
            ...OK.environments[0],
            status: 'circleci_unavailable',
            branch: null,
            message: 'Falta CIRCLECI_TOKEN en la config.',
          },
        ],
      });

    const { comp, el, fixture } = await consultar('repo');

    // El mensaje sigue en la fila…
    expect(el.querySelector('tbody tr')!.textContent).toContain('CIRCLECI_TOKEN');
    // …y hay algo para pedir la explicación sin volver a preguntar.
    const link = el.querySelector<HTMLButtonElement>('tbody button.linkish');
    expect(link).not.toBeNull();

    link!.click();
    await settle(fixture);
    expect(el.querySelector('.notice-modal')!.textContent).toContain('CIRCLECI_TOKEN=');
  });

  it('una fila que sí tiene rama no ofrece "cómo arreglarlo"', async () => {
    const { el } = await consultar('repo');

    // El selector es global ahora: el "Todos" del selector de ambientes también
    // es un `.linkish`. Lo que importa es que la fila no ofrezca el arreglo.
    expect(el.querySelectorAll('tbody button.linkish').length).toBe(0);
  });

  it('un 401 se explica como credencial rechazada, no como repo inexistente', async () => {
    answer = () =>
      Promise.reject(
        new HttpErrorResponse({
          status: 502,
          error: { detail: 'Bitbucket rechazó el token (401).' },
        }),
      );

    const { el } = await consultar('repo');

    const modal = el.querySelector('.notice-modal')!.textContent ?? '';
    expect(modal).toContain('rechazó las credenciales');
    // El arreglo dice el esquema: Bearer y nada más.
    expect(modal).toContain('Bearer');
    // Y el aviso NO manda a verificar el repo: con 401 el repo no es el problema.
    expect(modal).not.toContain('BITBUCKET_WORKSPACE sea el correcto');
  });

  it('el aviso de credenciales enlaza a la página del token', async () => {
    answer = () =>
      Promise.reject(
        new HttpErrorResponse({
          status: 502,
          error: { detail: 'Falta BITBUCKET_TOKEN en la config' },
        }),
      );

    const { el } = await consultar('repo');

    const link = el.querySelector<HTMLAnchorElement>('.notice-links a');
    expect(link?.href).toBe('https://id.atlassian.com/manage-profile/security/api-tokens');
    // En pestaña nueva necesita `rel` con noopener: sin eso la pestaña abierta
    // desde la app conserva una referencia a la de la app.
    expect(link?.getAttribute('target')).toBe('_blank');
    expect(link?.getAttribute('rel')).toContain('noopener');
  });

  it('sólo ofrece copiar la rama de una fila que la tiene', async () => {
    const { el } = await consultar('repo');

    const botones = el.querySelectorAll('app-copy-button');
    expect(botones.length).toBe(1);
  });

  it('muestra el mensaje del commit y el SHA corto debajo de la rama', async () => {
    // El mensaje es lo que dice qué cambió; el SHA corto identifica sin comer
    // media fila. El `title` lleva el mensaje entero porque acá va recortado.
    answer = () => Promise.resolve(OK_WITH_SUBJECT);

    const { el } = await consultar('repo');

    const fila = el.querySelector('tbody tr')!;
    expect(fila.textContent).toContain('fix: una cosa puntual');
    expect(fila.textContent).toContain('ca67134');
    expect(fila.textContent).not.toContain('ca67134f650e362133e51a9ffdb8e5ddc7fa53a5');
  });

  it('una fila sin mensaje de commit no inventa la línea', async () => {
    const { el } = await consultar('repo');

    // La fila de OK tiene `commit` pero no `commit_subject`: el SHA se muestra y
    // el mensaje no. Ningún elemento con `title` sin contenido, que sería una
    // pista falsa al pasar el mouse.
    const fila = el.querySelectorAll('tbody tr')[0];
    expect(fila.querySelector('.row-meta .mono')!.textContent).toBe('abc1234');
    expect(fila.querySelectorAll('[title]').length).toBe(0);
  });

  it('una fila desplegada por tag muestra el tag y de qué pipeline vino', async () => {
    // UAT/STG/PROD: el pipeline lo levanta `git push uat-210` y no trae rama.
    // La fila tiene que decir de dónde salió la rama, o parece que no se sabe.
    answer = () =>
      Promise.resolve({
        ...OK,
        environments: [
          {
            ...OK.environments[0],
            tag: 'uat-210',
            branch_from_pipeline: 210,
          },
        ],
      });

    const { el } = await consultar('repo');

    const fila = el.querySelector('tbody tr')!;
    expect(fila.querySelector('.tag-chip')!.textContent).toContain('uat-210');
    expect(fila.textContent).toContain('desde');
    expect(fila.textContent).toContain('210');
  });

  it('una fila de webhook no muestra "desde" porque no hay tag', async () => {
    // QA/DEV llegan por webhook: la rama viene del mismo pipeline. Poner un
    // "desde 210" ahí sería ruido.
    const { el } = await consultar('repo');

    expect(el.querySelector('.tag-chip')).toBeNull();
    expect(el.querySelector('tbody tr')!.textContent).not.toContain('desde');
  });

  // --- el catálogo de repositorios -------------------------------------------
  //
  // El input no es un campo de texto más: ofrece los repos del workspace y
  // decide cuándo volver a preguntarlos. Estos tests fijan las dos partes —lo
  // que se muestra mientras se escribe, y que un fallo del catálogo no rompa
  // el campo—, que es lo que se perdió cuando el buscador "no existía".

  /** La pantalla abierta, sin consultar: para mirar el input de repositorio. */
  async function abrir(): Promise<{
    comp: any;
    el: HTMLElement;
    fixture: ComponentFixture<DeploymentsPage>;
  }> {
    const fixture = TestBed.createComponent(DeploymentsPage);
    await settle(fixture);
    return { comp: fixture.componentInstance as any, el: fixture.nativeElement as HTMLElement, fixture };
  }

  /**
   * Lo que el dropdown ofrece, con el campo enfocado.
   *
   * Las sugerencias sólo se dibujan con el dropdown abierto —que es como se
   * comporta en el navegador—, así que enfocar es parte del arreglo.
   */
  async function opciones(
    fixture: ComponentFixture<DeploymentsPage>,
    el: HTMLElement,
  ): Promise<string[]> {
    el.querySelector<HTMLInputElement>('#repo')!.dispatchEvent(new Event('focus'));
    await settle(fixture);
    return [...el.querySelectorAll('app-repo-picker [role="option"]')].map((o) =>
      o.textContent!.trim(),
    );
  }

  describe('el catálogo de repositorios', () => {
    it('ofrece los repos del workspace mientras se escribe', async () => {
      const { el, fixture } = await abrir();

      expect(await opciones(fixture, el)).toEqual(CATALOG.repositories);
      expect(el.querySelector('.repos-hint')!.textContent).toContain('3 repositorios');
    });

    it('filtra por el texto escrito, y no sólo por el principio del nombre', async () => {
      // "refund" no empieza ningún nombre: está en el medio de uno. Buscar sólo
      // por prefijo no encontraría el repo que el que escribe está buscando.
      const { comp, fixture, el } = await abrir();
      comp.repo.set('refund');
      await settle(fixture);

      expect(await opciones(fixture, el)).toEqual(['yappy-trnxd-backend-refund-service']);
    });

    it('la copia local sirve aunque el backend no conteste', async () => {
      cachedCatalog = CATALOG;
      reposAnswer = () => new Promise<DeploymentReposResponse>(() => undefined);

      const { el, fixture } = await abrir();

      expect(await opciones(fixture, el)).toEqual(CATALOG.repositories);
    });

    it('si el backend falla se queda con la copia local y lo dice', async () => {
      cachedCatalog = CATALOG;
      reposAnswer = () => Promise.reject(new Error('backend caído'));

      const { el, fixture } = await abrir();

      expect(await opciones(fixture, el)).toEqual(CATALOG.repositories);
      expect(el.querySelector('.repos-hint')!.textContent).toContain('no se pudo actualizar');
      // El aviso del catálogo no puede abrir el modal: taparía el campo que se
      // está usando para escribir.
      expect(el.querySelector('app-notice-modal')).toBeNull();
    });

    it('sin copia ni backend el campo se sigue pudiendo usar a mano', async () => {
      reposAnswer = () => Promise.reject(new Error('backend caído'));

      const { el, fixture } = await abrir();

      expect(await opciones(fixture, el)).toEqual([]);
      expect(el.querySelector('.repos-hint')!.textContent).toContain('se escribe a mano');
      expect(el.querySelector<HTMLInputElement>('#repo')!.disabled).toBe(false);
    });

    it('el botón "Actualizar" le pide al backend que pregunte de nuevo', async () => {
      const { el, fixture } = await abrir();
      const boton = [...el.querySelectorAll('button')].find((b) =>
        b.textContent!.includes('Actualizar'),
      )!;

      boton.click();
      await settle(fixture);

      // La primera llamada es la de abrir la pantalla; la segunda, la del botón
      // y con `refresh`, que es lo único que muestra un repo creado hoy.
      expect(reposAsked).toEqual([false, true]);
    });

    it('una lista que el backend no pudo actualizar se muestra como vieja', async () => {
      reposAnswer = () => Promise.resolve({ ...CATALOG, stale: true });

      const { el, fixture } = await abrir();

      expect(await opciones(fixture, el)).toEqual(CATALOG.repositories);
      expect(el.querySelector('.repos-hint')!.textContent).toContain('no se pudo actualizar');
    });
  });
});
