import { Injectable, inject } from '@angular/core';
import { Api } from '../../api-gen/api';
import {
  compileDbObject,
  diffDbObject,
  executeSql,
  listDbObjects,
  listDbSchemas,
  migrateDbData,
  queryDb,
} from '../../api-gen/functions';
import {
  CompileRequest,
  CompileResponse,
  DbDiffRequest,
  DbObjectsResponse,
  DiffResponse,
  ExecuteRequest,
  ExecuteSqlResponse,
  MigrationRequest,
  MigrationResponse,
  QueryRequest,
  QueryResponse,
  SchemasResponse,
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

  /** Run one read-only statement and return its rows. */
  query(request: QueryRequest): Promise<QueryResponse> {
    return this.api.invoke(queryDb, { body: request });
  }

  /** Migrate every table involved in a query, honouring its joins and filters. */
  migrate(request: MigrationRequest): Promise<MigrationResponse> {
    return this.api.invoke(migrateDbData, { body: request });
  }
}