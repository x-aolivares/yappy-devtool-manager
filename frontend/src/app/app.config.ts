import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import {
  provideHttpClient,
  withFetch,
  withInterceptors,
} from '@angular/common/http';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { abortInterceptor } from './core/cancel';
import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding()),
    // `withInterceptors` es lo que hace que el `AbortSignal` que mandan las
    // páginas corte la petición: sin él la señal viaja en el contexto y no la
    // mira nadie. Ver `core/cancel`.
    provideHttpClient(withFetch(), withInterceptors([abortInterceptor]))
  ]
};
