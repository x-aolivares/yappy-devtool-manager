import { Injectable, inject } from '@angular/core';
import { Api } from '../../api-gen/api';
import { diffDbObject, executeSql, listDbSchemas } from '../../api-gen/functions';
import {
  DbDiffRequest,
  DiffResponse,
  ExecuteRequest,
  ExecuteSqlResponse,
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
}