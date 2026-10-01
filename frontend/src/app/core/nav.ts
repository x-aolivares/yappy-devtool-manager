/**
 * Navegación lateral agrupada por tipo de sección.
 *
 * El orden de las secciones y de los ítems es intencional: primero lo que se usa
 * para comparar regiones, después lo que escribe. Bitbucket y CircleCI todavía
 * no tienen herramientas en este repo, pero la sección queda visible para que la
 * arquitectura de información no haya que rehacerse cuando lleguen.
 */

export interface NavItem {
  readonly link: string;
  readonly label: string;
  /** Texto del `title`: aclara qué hace el ítem sin ocupar espacio en el nav. */
  readonly hint: string;
}

export interface NavSection {
  readonly id: string;
  readonly label: string;
  readonly items: readonly NavItem[];
  /** Se muestra cuando la sección todavía no tiene herramientas. */
  readonly emptyNote?: string;
}

export const NAV_SECTIONS: readonly NavSection[] = [
  {
    id: 'aws',
    label: 'AWS',
    items: [
      {
        link: '/params-read',
        label: 'Leer parámetros',
        hint: 'Lee parámetros de SSM y secretos de Secrets Manager',
      },
      {
        link: '/params-diff',
        label: 'Diff de parámetros',
        hint: 'Compara un parámetro o secreto entre dos ambientes',
      },
      {
        link: '/params-create',
        label: 'Crear parámetro',
        hint: 'Escribe un valor en varias regiones a la vez',
      },
      {
        link: '/params-edit',
        label: 'Editar parámetro',
        hint: 'Modifica el valor de un parámetro existente',
      },
      {
        link: '/sessions',
        label: 'Sesiones',
        hint: 'Revisa parámetros uno por uno con el progreso guardado',
      },
    ],
  },
  {
    id: 'database',
    label: 'Database',
    items: [
      {
        link: '/db-diff',
        label: 'Diff de base de datos',
        hint: 'Compara una tabla o stored procedure entre dos ambientes',
      },
      {
        link: '/compile',
        label: 'Compilar',
        hint: 'Compila una tabla o stored procedure de un ambiente a otro',
      },
      {
        link: '/sql',
        label: 'Ejecutar SQL',
        hint: 'Consulta información y migra los datos de lo consultado',
      },
    ],
  },
  {
    id: 'bitbucket',
    label: 'Bitbucket',
    items: [],
    emptyNote: 'Sin herramientas todavía',
  },
  {
    id: 'circleci',
    label: 'CircleCI',
    items: [],
    emptyNote: 'Sin herramientas todavía',
  },
];
