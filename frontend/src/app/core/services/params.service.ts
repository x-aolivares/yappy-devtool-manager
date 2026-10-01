import { Injectable, inject } from '@angular/core';
import { Api } from '../../api-gen/api';
import {
  paramsApply,
  paramsApplyExecute,
  paramsDiff,
  paramsGet,
  paramsMulti,
  paramsRead,
} from '../../api-gen/functions';
import {
  ApplyParamsRequest,
  CreateMultiParamsRequest,
  ExecuteParamsRequest,
  ExecuteParamsResponse,
  ParameterReadInfo,
  ParamsApplyResponse,
  ParamsDiffRequest,
  ParamsDiffResponse,
  ParamsMultiResponse,
  ParamsReadResponse,
  ReadParamsEntry,
} from '../../api-gen/models';

@Injectable({ providedIn: 'root' })
export class ParamsService {
  private readonly api = inject(Api);

  diff(request: ParamsDiffRequest): Promise<ParamsDiffResponse> {
    return this.api.invoke(paramsDiff, { body: request });
  }

  apply(request: ApplyParamsRequest): Promise<ParamsApplyResponse> {
    return this.api.invoke(paramsApply, { body: request });
  }

  applyExecute(request: ExecuteParamsRequest): Promise<ExecuteParamsResponse> {
    return this.api.invoke(paramsApplyExecute, { body: request });
  }

  multi(request: CreateMultiParamsRequest): Promise<ParamsMultiResponse> {
    return this.api.invoke(paramsMulti, { body: request });
  }

  get(env: string, name: string): Promise<ParameterReadInfo> {
    return this.api.invoke(paramsGet, { env, name });
  }

  read(envs: string[], entries: Array<ReadParamsEntry | string>): Promise<ParamsReadResponse> {
    return this.api.invoke(paramsRead, { body: { envs, entries } });
  }
}