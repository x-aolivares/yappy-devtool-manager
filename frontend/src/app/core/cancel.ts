import { HttpContext, HttpContextToken, HttpEvent, HttpInterceptorFn } from '@angular/common/http';
import { Observable } from 'rxjs';

/**
 * Cancelar la operación que una página tiene en vuelo.
 *
 * El cliente generado devuelve promesas, y una promesa no se cancela: no hay
 * `unsubscribe` en un `then`. Lo que sí se puede cortar es la petición HTTP, y
 * para llegar ahí hay que atravesar tres capas que no dejan pasar la señal:
 *
 * 1. **La función generada** sólo acepta un `HttpContext` como cuarto parámetro.
 *    Por eso la señal viaja en el contexto y no como argumento de la API.
 * 2. **`Api.invoke` resuelve con `firstValueFrom`**, que se queda suscrito hasta
 *    que llega el primer valor. Nadie desuscribe mientras tanto, y el backend de
 *    `fetch` de Angular aborta la petición **cuando se desuscribe**. O sea: la
 *    desuscripción hay que provocarla, y `abortInterceptor` es quien lo hace.
 * 3. **El backend ya puede haber empezado a trabajar.** Abortar el request corta
 *    la conexión, no el `SELECT` que el Python está corriendo: por eso el botón
 *    de una operación que escribe no dice "Cancelar" (ver `BusyModal`).
 *
 * Ninguna de las dos últimas es excusa para no hacerlo. Un `fetch` colgado
 * esperando un túnel que no responde mantiene el request abierto, ocupa un
 * connection slot del navegador y deja la página en `busy` sin salida; cortar la
 * espera es lo que el usuario puede pedir y lo que el navegador sabe hacer.
 */
export const ABORT = new HttpContextToken<AbortSignal | null>(() => null);

/**
 * El contexto de una llamada cancelable. `undefined` —lo que devuelven las
 * llamadas sin señal— deja la petición como estaba.
 */
export function abortCtx(signal?: AbortSignal): HttpContext | undefined {
  return signal ? new HttpContext().set(ABORT, signal) : undefined;
}

/**
 * El error con el que el interceptor corta la promesa.
 *
 * Es una clase propia y no un `status` porque "el usuario cortó la espera" y
 * "la consulta falló" son dos hechos distintos que la pantalla tiene que tratar
 * distinto: el primero no muestra un error, el segundo sí. Pasarlo por
 * `toApiError` lo convertiría en un mensaje que el usuario no puede actuar.
 */
export class RequestCancelled extends Error {
  constructor() {
    super('La operación se canceló.');
    this.name = 'RequestCancelled';
  }
}

/** ¿Este error es sólo "la cortó el usuario"? Ver `RequestCancelled`. */
export function isCancellation(err: unknown): boolean {
  return err instanceof RequestCancelled;
}

/**
 * Un `abort()` se vuelve el fin de la petición y el rechazo de la promesa.
 *
 * `HttpClient` no recibe una señal: aborta cuando se desuscribe. Escuchar la
 * señal del contexto y desuscribir `next(req)` es exactamente eso, y lo que hace
 * que el `fetch` corte la conexión de verdad.
 *
 * El `subscriber.error` es lo que no se puede omitir: desuscribirse solo dejaría
 * la promesa de `firstValueFrom` esperando un valor que no va a llegar, y la
 * página quedaría en `busy` para siempre. Se rechaza con `RequestCancelled`, que
 * las páginas reconocen para no mostrar un error.
 */
export const abortInterceptor: HttpInterceptorFn = (req, next) => {
  const signal = req.context.get(ABORT);
  if (!signal) return next(req);

  return new Observable<HttpEvent<unknown>>((subscriber) => {
    // Un objeto y no `subscriber` directo, a propósito: rxjs usa un `Subscriber`
    // que se le pasa como si fuera el suscriptor, así que desuscribir esa cadena
    // cerraría también al de acá y el `error` de `cut` —que es lo que rechaza la
    // promesa— quedaría sin efecto. Con un observer plano son dos.
    const subscription = next(req).subscribe({
      next: (event) => subscriber.next(event),
      error: (err) => subscriber.error(err),
      complete: () => subscriber.complete(),
    });

    const cut = (): void => {
      signal.removeEventListener('abort', cut);
      subscription.unsubscribe();
      subscriber.error(new RequestCancelled());
    };

    // Ya abortada al llegar: la carrera de un clic rápido sobre una petición
    // repetida. Chequearlo ahora es lo que evita que quede esperando para
    // siempre, porque `addEventListener` de un `abort()` ya ocurrido no dispara.
    if (signal.aborted) {
      cut();
      return;
    }

    signal.addEventListener('abort', cut);
    return () => {
      signal.removeEventListener('abort', cut);
      subscription.unsubscribe();
    };
  });
};

/**
 * Una operación a la vez, con un botón para cortarla.
 *
 * Una por página que muestra el modal de espera. `begin` abre la ranura y
 * devuelve la señal que viaja a la petición; el `cancel` del botón la aborta;
 * `finish` la cierra.
 *
 * Lo que `finish` devuelve es lo que evita el error clásico de estas páginas: una
 * respuesta que llegó tarde escribiendo sobre el estado de la operación que la
 * reemplazó. Sin ese `true`, un `busy.set(false)` de la respuesta vieja apagaría
 * el modal de la operación nueva.
 */
export class CancelSlot {
  private controller: AbortController | null = null;

  /**
   * Abre la ranura y devuelve la señal para abortarla.
   *
   * Aborta lo anterior si todavía estaba vivo: no hay dos operaciones en vuelo
   * en la misma página, y la que quedó fuera de la ranura no escribe nada.
   */
  begin(): AbortSignal {
    this.controller?.abort();
    this.controller = new AbortController();
    return this.controller.signal;
  }

  /**
   * Cierra la operación en vuelo.
   *
   * `false` si la señal no es la vigente —la respuesta es de una operación que ya
   * no existe y no escribe nada—, `true` si era la última y quedó cerrada.
   */
  finish(signal: AbortSignal): boolean {
    if (this.controller?.signal !== signal) return false;
    this.controller = null;
    return true;
  }

  /**
   * Corta lo que haya en vuelo. `false` si no había nada: un clic de más no
   * cambia nada.
   */
  cancel(): boolean {
    const controller = this.controller;
    this.controller = null;
    if (!controller) return false;
    controller.abort();
    return true;
  }
}