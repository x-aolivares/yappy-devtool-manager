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

@Injectable({ providedIn: 'root' })
export class DbService {
  private readonly api = inject(Api);

  diff(request: DbDiffRequest): Promise<DiffResponse> {
    return this.api.invoke(diffDbObject, { body: request });
  }

  executeSql(request: ExecuteRequest): Promise<ExecuteSqlResponse> {
    return this.api.invoke(executeSql, { body: request });
  }

  listSchemas(env: string): Promise<SchemasResponse> {
    return this.api.invoke(listDbSchemas, { env });
  }

  /** The tables ("table") or stored procedures ("procedure") of one schema. */
  listObjects(env: string, schema: string, objectType: string): Promise<DbObjectsResponse> {
    return this.api.invoke(listDbObjects, { env, schema, object_type: objectType });
  }
  /** Generate the script that would take an object from source into destination. */
  compile(request: CompileRequest): Promise<CompileResponse> {
    return this.api.invoke(compileDbObject, { body: request });
  }

  /** The bulk sibling of `compile`: one script for a whole schema. Also generate-only. */
  compileSchema(request: SchemaCompileRequest): Promise<SchemaCompileResponse> {
    return this.api.invoke(compileDbSchema, { body: request });
  }

  /** Run one read-only statement and return its rows. */
  query(request: QueryRequest): Promise<QueryResponse> {
    return this.api.invoke(queryDb, { body: request });
  }

  /** Migrate every table involved in a query, honouring its joins and filters. */
  migrate(request: MigrationRequest): Promise<MigrationResponse> {
    return this.api.invoke(migrateDbData, { body: request });
  }

  /**
   * The DATE/DATETIME/TIMESTAMP columns one table can be filtered by.
   *
   * Asked before migrating, so the picker offers real column names instead of
   * asking the user to remember them. An empty list is the answer that says
   * "this table migrates whole", not an error.
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
  migrateTables(request: TableMigrateRequest): Promise<MigrationResponse> {
    return this.api.invoke(migrateDbTableList, { body: request });
  }
}