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
import { abortCtx } from '../cancel';

/**
 * Igual que en `DbService`: la `signal` opcional es para el botón de cancelar del
 * modal de espera, y las llamadas que no la pasan quedan como estaban.
 *
 * `apply` es la excepción: no lleva señal porque no aparece en un modal. Genera
 * el comando a medida que se edita, con 350 ms de debounce y su propio token de
 * secuencia (`applySeq`) — una respuesta vieja ya no escribe nada, y no hay nada
 * que el usuario pueda cortar a mitad de camino.
 */
@Injectable({ providedIn: 'root' })
export class ParamsService {
  private readonly api = inject(Api);

  diff(request: ParamsDiffRequest, signal?: AbortSignal): Promise<ParamsDiffResponse> {
    return this.api.invoke(paramsDiff, { body: request }, abortCtx(signal));
  }

  apply(request: ApplyParamsRequest): Promise<ParamsApplyResponse> {
    return this.api.invoke(paramsApply, { body: request });
  }

  applyExecute(request: ExecuteParamsRequest, signal?: AbortSignal): Promise<ExecuteParamsResponse> {
    return this.api.invoke(paramsApplyExecute, { body: request }, abortCtx(signal));
  }

  multi(request: CreateMultiParamsRequest, signal?: AbortSignal): Promise<ParamsMultiResponse> {
    return this.api.invoke(paramsMulti, { body: request }, abortCtx(signal));
  }

  get(env: string, name: string, signal?: AbortSignal): Promise<ParameterReadInfo> {
    return this.api.invoke(paramsGet, { env, name }, abortCtx(signal));
  }

  read(
    envs: string[],
    entries: Array<ReadParamsEntry | string>,
    signal?: AbortSignal,
  ): Promise<ParamsReadResponse> {
    return this.api.invoke(paramsRead, { body: { envs, entries } }, abortCtx(signal));
  }
}