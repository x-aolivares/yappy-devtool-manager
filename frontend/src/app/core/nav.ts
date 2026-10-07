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
    // Diff, alta/edición y Sesiones salen del menú. No es que el código se haya
    // ido: sigue acá, y `/sessions` sigue enrutada a propósito porque "Leer
    // parámetros" crea una sesión, que queda en su historial aunque la pantalla
    // ya no enlace a esa vista. Por eso el `SessionService` se queda en
    // `params-read`: si algún día se borran las sesiones de verdad, hay que
    // sacar el `SessionService` en el mismo cambio.
    items: [
      {
        link: '/params-read',
        label: 'Leer parámetros',
        hint: 'Lee parámetros de SSM y secretos de Secrets Manager',
      },
    ],
  },
  {
    id: 'database',
    label: 'Database',
    items: [
      {
        link: '/sql',
        label: 'Ejecutar SQL',
        hint: 'Consulta información y migra los datos de lo consultado',
      },
      {
        link: '/migrate-data',
        label: 'Migrar datos',
        hint: 'Copia un conjunto de tablas de un ambiente a otro, con filtro por fecha por tabla',
      },
      {
        link: '/compile',
        label: 'Compilar',
        hint: 'Compila una tabla o stored procedure de un ambiente a otro',
      },
      {
        link: '/schema-sync',
        label: 'Sincronizar schema',
        hint: 'Lleva todas las tablas y stored procedures de un esquema a otro ambiente',
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
