/**
 * Tipos que reflejan los DTOs de `web/api/schemas.py`.
 *
 * Se replican a mano en vez de generarse desde el OpenAPI para no agregar una
 * dependencia de build. El contrato es estable y esta lista hace explícito qué
 * espera la UI del backend; si cambia un campo, TypeScript avisa acá.
 */

export interface Environment {
  name: string;
  aws_profile: string | null;
  aws_region: string | null;
}

export interface Parameter {
  key: string;
  value: string;
  is_json: boolean;
  environment: string;
}

export interface ResolvedSecret {
  secret_name: string;
  value: string;
  is_json: boolean;
  environment: string;
}

export interface Schema {
  name: string;
  environment: string;
}

export type ObjectKind =
  | 'table'
  | 'view'
  | 'procedure'
  | 'function'
  | 'trigger';

export interface DbObject {
  name: string;
  kind: ObjectKind;
  schema: string;
}

export interface QueryResult {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  elapsed_ms: number;
  truncated: boolean;
}

export interface ConnectionInfo {
  target: string;
  host: string;
  port: number;
  user: string;
  reachable: boolean;
  detail: string;
  /** Encrypted either way; false means the server cert could not be validated. */
  tlsVerified?: boolean;
}

export interface LocalMysqlStatus {
  running: boolean;
  host: string;
  port: number;
  user: string;
  detail: string;
  start_command: string;
}

export interface MigrateObject {
  kind: ObjectKind;
  schema: string;
  name: string;
  ddl: string;
  environment: string;
  warnings: string[];
}

export interface MigrateResult {
  target_schema: string;
  statements_executed: number;
  statements_failed: number;
  applied: MigrateObject[];
  failures: [string, string][];
}

export interface MigrateRequest {
  schema: string;
  mode: string;
  objects: [string, string][];
  tables: string[];
  target_schema?: string | null;
}

/** Payload de error que produce `web/api/error_handlers.py`. */
export interface ApiError {
  code: string;
  message: string;
  detail: string | null;
}
