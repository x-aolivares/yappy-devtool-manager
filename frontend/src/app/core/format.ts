import { EnvironmentInfo } from '../api-gen/models';

export const STATUS_LABELS: Record<string, string> = {
  equal: 'Sin cambios',
  different: 'Hay cambios',
  replace_in_a: 'Se reemplaza en la región Destino',
  missing_in_a: 'Falta en la región Destino',
  missing_in_b: 'Falta en la región de Origen',
  none: 'No existe en ninguna región',
  schema_sync: 'Se sincroniza el schema en la región Destino',
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

/**
 * Ambientes que son de verdad ambientes de AWS.
 *
 * `local` (DB_MODE=local) se alcanza por TCP contra un MySQL local: no tiene
 * Parameter Store ni Secrets Manager, así que ofrecerlo en una página de SSM o
 * de Secrets sería una invitación a pegarle a AWS de verdad. El filtro va en la
 * página y no en `app-env-picker` a propósito: las páginas de base de datos sí
 * necesitan ver `local`.
 */
export function awsEnvironments(
  list: EnvironmentInfo[] | null | undefined,
): EnvironmentInfo[] | null {
  if (!list) return list ?? null;
  return list.filter((e) => !e.is_local);
}

export function objectLabel(type: string): string {
  return type === 'procedure' ? 'stored procedure' : 'tabla';
}

export function formatValue(v: unknown): string {
  if (v === null || v === undefined || v === '') return '';
  if (typeof v === 'string') {
    try {
      return JSON.stringify(JSON.parse(v), null, 2);
    } catch {
      return v;
    }
  }
  return JSON.stringify(v, null, 2);
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return (
    d.toLocaleDateString('es-AR') +
    ' ' +
    d.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' })
  );
}

export function preText(content: string | null | undefined, empty: string): string {
  return content ? content : empty;
}