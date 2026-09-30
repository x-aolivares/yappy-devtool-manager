/**
 * Cliente HTTP del backend.
 *
 * Todas las llamadas van a rutas relativas bajo `/api`, que el dev server de
 * Angular reenvía al FastAPI (ver `proxy.conf.json`). Así el browser nunca ve un
 * cross-origin: el CORS del backend existe para el caso de abrir la UI sin
 * proxy, no para el flujo normal.
 */
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, throwError } from 'rxjs';
import {
  ApiError,
  ConnectionInfo,
  DbObject,
  Environment,
  LocalMysqlStatus,
  MigrateObject,
  MigrateRequest,
  MigrateResult,
  Parameter,
  QueryResult,
  ResolvedSecret,
  Schema,
} from './models';

/** Error normalizado: cualquier fallo de la API llega a la UI con esta forma. */
export class ApiRequestError extends Error {
  readonly code: string;
  readonly detail: string | null;
  readonly status: number;

  constructor(raw: ApiError, status: number) {
    super(raw.message);
    this.name = 'ApiRequestError';
    this.code = raw.code;
    this.detail = raw.detail;
    this.status = status;
  }
}

const BASE = '/api';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  // --- environments & parameters -----------------------------------------

  listEnvironments(): Observable<Environment[]> {
    return this.http.get<Environment[]>(`${BASE}/environments`);
  }

  listParameters(env: string): Observable<Parameter[]> {
    return this.http.get<Parameter[]>(`${BASE}/parameters/${env}`);
  }

  /**
   * El valor crudo del parámetro ES el nombre del secreto; no hay convención
   * de prefijos ni búsqueda: la resolución solo ocurre al pedirla explícitamente.
   */
  resolveSecret(env: string, key: string): Observable<ResolvedSecret> {
    return this.http.post<ResolvedSecret>(`${BASE}/parameters/${env}/resolve`, {
      key,
    });
  }

  // --- databases ----------------------------------------------------------

  listSchemas(env: string): Observable<Schema[]> {
    return this.http.get<Schema[]>(`${BASE}/databases/${env}/schemas`);
  }

  listObjects(env: string, schema: string, kind?: string): Observable<DbObject[]> {
    const path = `${BASE}/databases/${env}/schemas/${schema}/objects`;
    return kind
      ? this.http.get<DbObject[]>(path, { params: { kind } })
      : this.http.get<DbObject[]>(path);
  }

  getConnection(env: string): Observable<ConnectionInfo> {
    return this.http.get<ConnectionInfo>(`${BASE}/databases/${env}/connection`);
  }

  // --- migration ----------------------------------------------------------

  previewMigration(env: string, req: MigrateRequest): Observable<MigrateObject[]> {
    return this.http.post<MigrateObject[]>(
      `${BASE}/databases/${env}/migrate/preview`,
      req,
    );
  }

  migrate(env: string, req: MigrateRequest): Observable<MigrateResult> {
    return this.http.post<MigrateResult>(`${BASE}/databases/${env}/migrate`, req);
  }

  // --- query --------------------------------------------------------------

  runQuery(
    env: string,
    sql: string,
    schema: string | null,
    target: 'env' | 'local',
  ): Observable<QueryResult> {
    return this.http.post<QueryResult>(`${BASE}/query/${env}`, {
      sql,
      schema,
      target,
    });
  }

  // --- local mysql --------------------------------------------------------

  localMysqlStatus(): Observable<LocalMysqlStatus> {
    return this.http.get<LocalMysqlStatus>(`${BASE}/local-mysql`);
  }

  startLocalMysql(): Observable<LocalMysqlStatus> {
    return this.http.post<LocalMysqlStatus>(`${BASE}/local-mysql/start`, {});
  }

  stopLocalMysql(): Observable<LocalMysqlStatus> {
    return this.http.post<LocalMysqlStatus>(`${BASE}/local-mysql/stop`, {});
  }

  /**
   * Envuelve una request para que los errores lleguen siempre como
   * `ApiRequestError`. Un 500 con HTML (por ejemplo, uvicorn sin encontrar la
   * app) se convierte igual en un error con mensaje legible en vez de un
   * `[object Object]` en pantalla.
   */
  safe<T>(source: Observable<T>): Observable<T> {
    return source.pipe(
      catchError((err: unknown) => throwError(() => toApiError(err))),
    );
  }
}

function toApiError(err: unknown): ApiRequestError {
  if (err instanceof HttpErrorResponse) {
    const body = err.error as Partial<ApiError> | string | null;

    if (body && typeof body === 'object' && 'code' in body && 'message' in body) {
      return new ApiRequestError(
        {
          code: body.code as string,
          message: body.message as string,
          detail: body.detail ?? null,
        },
        err.status,
      );
    }

    // Sin el payload de dominio: hay que explicitar qué pasó.
    const message =
      err.status === 0
        ? 'No se pudo contactar la API. ¿Está corriendo `yappy web api`?'
        : `Error ${err.status} del backend.`;
    const detail =
      typeof body === 'string' && body.length > 0
        ? body.slice(0, 600)
        : (err.message ?? null);

    return new ApiRequestError(
      { code: 'BACKEND_ERROR', message, detail },
      err.status,
    );
  }

  return new ApiRequestError(
    {
      code: 'UNKNOWN_ERROR',
      message: err instanceof Error ? err.message : String(err),
      detail: null,
    },
    0,
  );
}
