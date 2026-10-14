import { Injectable, inject } from '@angular/core';
import { Api } from '../../api-gen/api';
import { listEnvironments } from '../../api-gen/functions';
import { EnvironmentsResponse } from '../../api-gen/models';

@Injectable({ providedIn: 'root' })
export class EnvironmentService {
  private readonly api = inject(Api);

  /**
   * La lista de ambientes, compartida y resuelta una vez.
   *
   * Antes cada página lo pedía en su constructor, así que cambiar de pantalla
   * re-preguntaba lo mismo. La lista sale de `backend/config/env.*`, que cambia
   * cuando alguien agrega un ambiente — mientras esta pantalla está abierta, no.
   * `reload()` existe para el que quiera forzar la lectura de nuevo.
   */
  private pending: Promise<EnvironmentsResponse> | null = null;

  list(): Promise<EnvironmentsResponse> {
    this.pending ??= this.api.invoke(listEnvironments, {}).catch((err) => {
      // Un fallo no se cachea: si fue un rezo transitorio, el próximo que
      // pregunte tiene que poder volver a intentarlo.
      this.pending = null;
      throw err;
    });
    return this.pending;
  }

  reload(): Promise<EnvironmentsResponse> {
    this.pending = null;
    return this.list();
  }
}