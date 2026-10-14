/**
 * Los cinco estados de un recurso, y cómo se leen.
 *
 * El backend los manda como strings y esta es la única traducción del cliente:
 * si aparece un estado que no está acá, `label` cae en el string crudo y el
 * badge queda sin color en vez de romper la pantalla.
 *
 * `down` y `dead` se distinguen a propósito. `down` es que no hay nada: nadie
 * lo levantó. `dead` es que lo hubo y se fue — la fila ofrece "Levantar de
 * nuevo", no "Levantar", y la diferencia es si el developer tiene que buscar
 * por qué cayó.
 */
export type ResourceState = 'down' | 'starting' | 'up' | 'error' | 'dead';

const LABELS: Record<ResourceState, string> = {
  down: 'abajo',
  starting: 'levantando…',
  up: 'arriba',
  error: 'no levantó',
  dead: 'cayó',
};

/** El texto de la celda de estado. `since` es un timestamp crudo del backend. */
export function stateLabel(state: string, since: number | null): string {
  const label = LABELS[state as ResourceState] ?? state;
  if ((state === 'up' || state === 'dead') && since) {
    return `${label} · ${sinceLabel(since)}`;
  }
  return label;
}

/** El texto del badge corto. Va sin timestamp: un badge con dos datos no cabe. */
export function stateBadge(state: string): string {
  return LABELS[state as ResourceState] ?? state;
}

/** La clase de `.badge` según el estado. Reusa la escala que ya existe. */
export function stateBadgeClass(state: string): string {
  switch (state) {
    case 'up':
      return 'ok';
    case 'starting':
    case 'dead':
      return 'warn';
    case 'error':
      return 'error';
    default:
      return 'off';
  }
}

/** "hace 2 h 14 min". El backend manda epoch; el locale es cosa del cliente. */
export function sinceLabel(since: number): string {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - since));
  if (seconds < 60) return `hace ${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `hace ${hours} h ${minutes % 60} min`;
  return `hace ${Math.floor(hours / 24)} d`;
}

/**
 * El texto de la línea de detalle de una tarjeta.
 *
 * `detail` es opcional porque las filas del catálogo no lo tienen: el catálogo
 * dice el puerto y el nombre, y no hay una frase que intrigue. Sin ella, la
 * línea muestra lo que hay — pid y antigüedad — y nada más.
 */
export function resourceMeta(row: {
  detail?: string;
  state: string;
  pid?: number | null;
  since?: number | null;
}): string {
  const detail = row.detail ?? '';
  switch (row.state) {
    case 'starting':
      return detail || 'verificando que responda';
    case 'up':
      return [detail, row.pid ? `pid ${row.pid}` : '', row.since ? sinceLabel(row.since) : '']
        .filter(Boolean)
        .join(' · ');
    case 'dead':
      return row.pid
        ? `el proceso ${row.pid} terminó · ${row.since ? sinceLabel(row.since) : 'hace un rato'}`
        : 'el proceso terminó · hace un rato';
    default:
      return detail;
  }
}

/** La frase que el `aria-live` anuncia cuando un recurso cambia de estado. */
export function stateAnnouncement(name: string, state: string): string {
  switch (state) {
    case 'starting':
      return `${name}: levantando`;
    case 'up':
      return `${name}: arriba`;
    case 'error':
      return `${name}: no levantó`;
    case 'dead':
      return `${name}: se cayó`;
    case 'down':
      return `${name}: abajo`;
    default:
      return `${name}: ${state}`;
  }
}

/**
 * Los estados donde el botón principal es "levantar".
 *
 * `dead` está con `down` y no con `up` a propósito: la fila muestra "Levantar de
 * nuevo" y el botón hace lo mismo que en `down`. Lo que cambia es el texto, que
 * es información: si se cayó hay algo que mirar en el log.
 */
export function canStart(state: string): boolean {
  return state === 'down' || state === 'dead' || state === 'error';
}

/** El rótulo del botón principal según el estado. */
export function startLabel(state: string): string {
  switch (state) {
    case 'dead':
      return 'Levantar de nuevo';
    case 'error':
      return 'Reintentar';
    default:
      return 'Levantar';
  }
}

/** Sólo se abre una URL contra un servicio que respondió. */
export function canOpen(state: string): boolean {
  return state === 'up';
}

/** El log tiene sentido mientras corre o mientras se lee por qué no corrió. */
export function canShowLogs(state: string): boolean {
  return state === 'up' || state === 'starting';
}