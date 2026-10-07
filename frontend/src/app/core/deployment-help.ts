/**
 * Por qué el modal explica el error en vez de repetirlo.
 *
 * `toApiError` devuelve el `detail` del backend, que es un texto pensado para
 * quien lee el código: "Falta BITBUCKET_TOKEN" dice qué falta, no qué hacer.
 * Un aviso que nombra una variable de configuración y se corta ahí le deja al
 * usuario la parte difícil —dónde se pone— sin decirsela.
 *
 * Y hay un caso que ni siquiera es un error de la consulta: si el backend es
 * viejo, el catch-all de la SPA responde 405 a un POST y el mensaje es
 * "Method Not Allowed", que no dice nada. Traducirlo es parte del trabajo: si no
 * se puede arreglar, hay que explicar qué significa.
 */

/** Dónde se genera cada credencial. Sin esto el aviso dice qué falta, no cómo se consigue. */
interface TokenLink {
  label: string;
  href: string;
}

/** Config que hay que cargar y dónde va. Es la respuesta a la mayoría de los 502. */
const CONFIG_HELP: Array<{
  needle: RegExp;
  keys: string[];
  where: string;
  links?: TokenLink[];
}> = [
  {
    // El 401 entra por acá también: nombra la misma variable, pero el arreglo es
    // distinto y lo dice el título.
    needle: /BITBUCKET_TOKEN|BITBUCKET_(USERNAME|APP_PASSWORD)|401/i,
    keys: ['BITBUCKET_TOKEN'],
    where: 'backend/config/env.base',
    links: [
      {
        label: 'Generar el API token de Atlassian',
        href: 'https://id.atlassian.com/manage-profile/security/api-tokens',
      },
    ],
  },
  {
    needle: /CIRCLECI_TOKEN/,
    keys: ['CIRCLECI_TOKEN'],
    where: 'backend/config/env.base',
    links: [
      {
        label: 'Generar el API token de CircleCI',
        href: 'https://app.circleci.com/settings/user/tokens',
      },
    ],
  },
  {
    needle: /CIRCLECI_SLUG_PREFIX|BITBUCKET_WORKSPACE/,
    // Sólo se completan si el proyecto no está donde la app asume; por eso no
    // lleva link a dónde generarlos: no hay nada que generar, hay que copiar
    // el valor de una URL que ya tenés abierta.
    keys: ['BITBUCKET_WORKSPACE', 'CIRCLECI_SLUG_PREFIX'],
    where: 'backend/config/env.base',
  },
];

export interface FailureExplained {
  /** Titular del modal: qué pasó, en una línea. */
  title: string;
  /** El mensaje crudo del backend, para el que quiera el detalle. */
  detail: string;
  /** Qué hacer, en imperativo y en orden. Vacío si no hay nada que agregar. */
  steps: string[];
  /** Texto para el clipboard: algo que se pueda pegar y funcione. */
  snippet?: string;
  /** A dónde ir a conseguir la credencial que falta. */
  links?: TokenLink[];
}

/**
 * Traduce el fallo de una consulta a algo accionable.
 *
 * `status` es el código HTTP: hace falta para separar "el backend no tiene la
 * ruta" de "el backend respondió que le falta una credencial", que son dos
 * situaciones con arreglos opuestos.
 */
export function explainDeploymentFailure(
  status: number | undefined,
  message: string,
): FailureExplained {
  const detail = message?.trim() || `Error ${status ?? 'desconocido'}`;

  // 405 desde una ruta que sí debería existir = backend corriendo sin esta ruta.
  // Es el caso de "levantá y probá": el proceso se importó antes de que la ruta
  // existiera y Python no recarga módulos.
  if (status === 405 || /Method Not Allowed/i.test(detail)) {
    return {
      title: 'El backend no tiene esta pantalla todavía',
      detail,
      steps: [
        'El servidor web se levantó antes de que existiera el endpoint, y Python no recarga el código solo.',
        'Cortá el comando (Ctrl+C) y volvé a correr `yappy web --watch`.',
        'El frontend no hace falta reiniciarlo: esa parte se recompila sola.',
      ],
      snippet: 'Ctrl+C   yappy web --watch',
    };
  }

  for (const help of CONFIG_HELP) {
    if (!help.needle.test(detail)) continue;
    // 401 no es "falta la variable": la variable está y no sirve. El arreglo es
    // otro —el usuario es el email, o el token tiene basura pegada— y decir
    // "agregá esto" mandaría a hacer algo que ya hicieron.
    const rejected = /401/i.test(detail);
    return {
      title: rejected
        ? 'Bitbucket rechazó las credenciales'
        : 'Faltan credenciales en la configuración',
      detail,
      steps: rejected
        ? [
            'El token va como Bearer: no hacen falta usuario ni password.',
            'Revisá que no tenga espacios copiados de más: el final con espacio es el error más común al pegar.',
            'Verificá que el token siga vigente en la página de Atlassian.',
          ]
        : [
            `Agregá estas variables a ${help.where}:`,
            ...help.keys.map((key) => `${key}=`),
            'El archivo no se commitea, así que la clave queda sólo en tu máquina.',
          ],
      snippet: `${help.keys.map((k) => `${k}=`).join('\n')}\n# en ${help.where}`,
      links: help.links,
    };
  }

  if (status === 502) {
    return {
      title: 'No se pudo consultar Bitbucket',
      detail,
      steps: [
        'El backend respondió pero la cadena se cortó antes de llegar a AWS.',
        'Revisá que el repositorio exista y que BITBUCKET_WORKSPACE sea el correcto.',
        'Si es un proyecto que no es de ECS, esta pantalla todavía no lo cubre.',
      ],
    };
  }

  // 404 desde Bitbucket es deliberadamente opaco: sin acceso a un repo privado
  // responde lo mismo que si no existiera. Por eso el mensaje habla de las dos
  // cosas a la vez en vez de afirmar que el repo no existe.
  if (status === 404) {
    return {
      title: 'Bitbucket no devuelve ese repositorio',
      detail,
      steps: [
        'Bitbucket responde igual si el repo no existe y si no tenés acceso: por eso no se puede distinguir.',
        'Revisá que BITBUCKET_WORKSPACE y el nombre del repo estén bien escritos.',
        'Verificá que la cuenta del token tenga lectura sobre ese repositorio.',
      ],
    };
  }

  if (status === 404) {
    return {
      title: 'No existe esa ruta en el backend',
      detail,
      steps: ['El servidor web está corriendo sin esta pantalla. Reiniciá `yappy web --watch`.'],
      snippet: 'yappy web --watch',
    };
  }

  return { title: 'No se pudo consultar', detail, steps: [] };
}
