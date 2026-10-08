import { Injectable, inject } from '@angular/core';
import { Api } from '../../api-gen/api';
import {
  compileDbObject,
  compileDbSchema,
  diffDbObject,
  executeSql,
  listDbDateColumns,
  listDbObjects,
  listDbSchemas,
  migrateDbData,
  migrateDbTableList,
  queryDb,
} from '../../api-gen/functions';
import {
  CompileRequest,
  CompileResponse,
  DateColumnsResponse,
  DbDiffRequest,
  DbObjectsResponse,
  DiffResponse,
  ExecuteRequest,
  ExecuteSqlResponse,
  MigrationRequest,
  MigrationResponse,
  QueryRequest,
  QueryResponse,
  SchemaCompileRequest,
  SchemaCompileResponse,
  SchemasResponse,
  TableMigrateRequest,
} from '../../api-gen/models';
import { abortCtx } from '../cancel';

/**
 * Las llamadas que una página muestra en el modal de espera aceptan una `signal`
 * para que el botón de cancelar aborte la petición.
 *
 * El parámetro va **al final y es opcional** a propósito: las que no lo pasan
 * —las lecturas perezosas de esquemas y de tablas, que viven en un spinner en
 * línea y no en un modal— siguen siendo promesas sin más, y `undefined` deja la
 * petición exactamente como estaba.
 */
@Injectable({ providedIn: 'root' })
export class DbService {
  private readonly api = inject(Api);

  diff(request: DbDiffRequest, signal?: AbortSignal): Promise<DiffResponse> {
    return this.api.invoke(diffDbObject, { body: request }, abortCtx(signal));
  }

  executeSql(request: ExecuteRequest, signal?: AbortSignal): Promise<ExecuteSqlResponse> {
    return this.api.invoke(executeSql, { body: request }, abortCtx(signal));
  }

  listSchemas(env: string): Promise<SchemasResponse> {
    return this.api.invoke(listDbSchemas, { env });
  }

  /** The tables ("table") or stored procedures ("procedure") of one schema. */
  listObjects(env: string, schema: string, objectType: string): Promise<DbObjectsResponse> {
    return this.api.invoke(listDbObjects, { env, schema, object_type: objectType });
  }
  /** Generate the script that would take an object from source into destination. */
  compile(request: CompileRequest, signal?: AbortSignal): Promise<CompileResponse> {
    return this.api.invoke(compileDbObject, { body: request }, abortCtx(signal));
  }

  /** The bulk sibling of `compile`: one script for a whole schema. Also generate-only. */
  compileSchema(request: SchemaCompileRequest, signal?: AbortSignal): Promise<SchemaCompileResponse> {
    return this.api.invoke(compileDbSchema, { body: request }, abortCtx(signal));
  }

  /** Run one read-only statement and return its rows. */
  query(request: QueryRequest, signal?: AbortSignal): Promise<QueryResponse> {
    return this.api.invoke(queryDb, { body: request }, abortCtx(signal));
  }

  /** Migrate every table involved in a query, honouring its joins and filters. */
  migrate(request: MigrationRequest, signal?: AbortSignal): Promise<MigrationResponse> {
    return this.api.invoke(migrateDbData, { body: request }, abortCtx(signal));
  }

  /**
   * The DATE/DATETIME/TIMESTAMP columns one table can be filtered by.
   *
   * Asked before migrating, so the picker offers real column names instead of
   * asking the user to remember them. An empty list is the answer that says
   * "this table migrates whole", not an error.
   *
   * Sin `signal`: son las columnas de una fila de la grilla, se piden perezosas
   * al marcar la tabla y cada fila tiene su token de secuencia. No hay modal que
   * cerrar.
   */
  dateColumns(env: string, schema: string, table: string): Promise<DateColumnsResponse> {
    return this.api.invoke(listDbDateColumns, { env, schema, table });
  }

  /**
   * Migrate an explicit list of tables, each with its own window.
   *
   * The bulk sibling of `migrate`: same engine, same response, but the source of
   * each `SELECT` is this list instead of a user-written query.
   */
  migrateTables(request: TableMigrateRequest, signal?: AbortSignal): Promise<MigrationResponse> {
    return this.api.invoke(migrateDbTableList, { body: request }, abortCtx(signal));
  }
}