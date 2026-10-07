/**
 * El modal tiene que explicar el arreglo, no repetir el mensaje del backend.
 *
 * "Falta BITBUCKET_USERNAME" dice qué falta y nada más: el usuario sigue sin
 * saber dónde se pone. Y "Method Not Allowed" no dice ni siquiera eso — es la
 * respuesta del catch-all de la SPA cuando el backend es viejo, y sin traducirla
 * es un callejón sin salida.
 */
import { explainDeploymentFailure } from './deployment-help';

describe('explainDeploymentFailure', () => {
  it('un 405 significa backend viejo, y el arreglo es reiniciarlo', () => {
    const fail = explainDeploymentFailure(405, 'Method Not Allowed');

    expect(fail.title).toContain('todavía');
    // El arreglo tiene que nombrar el comando, no sólo decir "reiniciá".
    expect(fail.steps.join(' ')).toContain('yappy web --watch');
  });

  it('traduce el 405 aunque venga sin status, por el texto', () => {
    // El `detail` de un 405 es siempre "Method Not Allowed", así que el texto
    // alcanza cuando el status no viaja.
    expect(explainDeploymentFailure(undefined, 'Method Not Allowed').title).toContain('todavía');
  });

  it('una credencial que falta dice cuáles y en qué archivo', () => {
    const fail = explainDeploymentFailure(502, 'Falta BITBUCKET_TOKEN en la config');

    expect(fail.title).toBe('Faltan credenciales en la configuración');
    expect(fail.steps.join(' ')).toContain('BITBUCKET_TOKEN');
    expect(fail.steps.join(' ')).toContain('backend/config/env.base');
  });

  it('el snippet lista las variables listas para pegar', () => {
    const fail = explainDeploymentFailure(502, 'Falta BITBUCKET_TOKEN');

    expect(fail.snippet).toContain('BITBUCKET_TOKEN=');
    expect(fail.snippet).toContain('env.base');
  });

  it('el token de CircleCI se explica aparte', () => {
    const fail = explainDeploymentFailure(502, 'Falta CIRCLECI_TOKEN en la config');

    expect(fail.snippet).toContain('CIRCLECI_TOKEN=');
  });

  it('el aviso de Bitbucket enlaza a donde se genera el token', () => {
    // El aviso dice QUÉ falta; sin el link falta el CÓMO se consigue, que es
    // la parte que obliga a salir de la app a buscar.
    const fail = explainDeploymentFailure(502, 'Falta BITBUCKET_TOKEN en la config');

    expect(fail.links).toEqual([
      {
        label: expect.stringContaining('Atlassian'),
        href: 'https://id.atlassian.com/manage-profile/security/api-tokens',
      },
    ]);
  });

  it('el aviso de CircleCI enlaza a su propia página de tokens', () => {
    const fail = explainDeploymentFailure(502, 'Falta CIRCLECI_TOKEN en la config');

    expect(fail.links).toEqual([
      {
        label: expect.stringContaining('CircleCI'),
        href: 'https://app.circleci.com/settings/user/tokens',
      },
    ]);
  });

  it('un 401 no dice "agregá la variable" sino cómo corregirla', () => {
    // La variable ya está cargada: es la que se está rechazando. Mandar a
    // cargarla de nuevo hace que el usuario repita lo que ya hizo.
    const fail = explainDeploymentFailure(502, 'Bitbucket rechazó el token (401).');

    expect(fail.title).toBe('Bitbucket rechazó las credenciales');
    expect(fail.steps.join(' ')).not.toContain('Agregá estas variables');
  });

  it('un 401 aclara que va como Bearer, sin usuario', () => {
    // El error anterior venía de mandar Basic con usuario: el arreglo tiene que
    // decir el esquema, no sólo "probá otra vez".
    const fail = explainDeploymentFailure(502, 'Bitbucket rechazó el token (401).');

    expect(fail.steps.join(' ')).toContain('Bearer');
  });

  it('un 401 de Bitbucket muestra el link del token, no el de reiniciar', () => {
    // El 401 llega con 502 desde el backend, pero el detalle trae el 401.
    const fail = explainDeploymentFailure(502, 'Bitbucket rechazó las credenciales (401)');

    expect(fail.links?.[0]?.href).toBe(
      'https://id.atlassian.com/manage-profile/security/api-tokens',
    );
  });

  it('un 404 no afirma que el repo no exista', () => {
    // Bitbucket responde igual ante las dos cosas, así que el aviso que asegura
    // "no existe" manda a verificar el nombre cuando el problema es el acceso.
    const fail = explainDeploymentFailure(404, 'Not Found');

    expect(fail.title).not.toContain('no existe');
    expect(fail.steps.join(' ')).toContain('acceso');
  });

  it('las variables que no se generan no inventan un link', () => {
    // `BITBUCKET_WORKSPACE` no es una credencial: se copia de una URL que ya
    // tenés. Enmandarlo a una página de tokens sería mandar al lugar errado.
    const fail = explainDeploymentFailure(502, 'Revisá BITBUCKET_WORKSPACE');

    expect(fail.links).toBeUndefined();
  });

  it('un 502 sin nombre de variable no inventa uno', () => {
    const fail = explainDeploymentFailure(502, 'Bitbucket respondió 500 Internal Server Error');

    // El arreglo genérico, sin inventar claves.
    expect(fail.snippet).toBeUndefined();
    expect(fail.steps.length).toBeGreaterThan(0);
  });

  it('un error desconocido no rompe y no inventa pasos', () => {
    const fail = explainDeploymentFailure(undefined, 'algo raro');

    expect(fail.detail).toBe('algo raro');
    expect(fail.steps).toEqual([]);
  });

  it('un mensaje vacío cae en algo legible en vez de vacío', () => {
    const fail = explainDeploymentFailure(500, '');

    expect(fail.detail).toContain('500');
  });
});
