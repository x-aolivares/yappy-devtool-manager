/**
 * `core/cancel` es la pieza que hace que el botón del modal de espera haga algo
 * y no sea decorativo, así que se prueba por las dos puntas: que la señal viaje
 * en el `HttpContext` y corte la suscripción, y que `CancelSlot` no deje dos
 * operaciones peleándose por el estado de la página.
 */
import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';
import {
  ABORT,
  CancelSlot,
  abortCtx,
  abortInterceptor,
  isCancellation,
  RequestCancelled,
} from './cancel';

describe('CancelSlot', () => {
  it('begin devuelve una señal viva', () => {
    const slot = new CancelSlot();
    expect(slot.begin().aborted).toBe(false);
  });

  it('begin aborta la operación anterior si seguía viva', () => {
    // Es lo que impide que dos clicks seguidos dejen dos respuestas escribiendo
    // sobre el mismo estado de la página.
    const slot = new CancelSlot();
    const primera = slot.begin();
    slot.begin();

    expect(primera.aborted).toBe(true);
  });

  it('finish cierra la operación vigente y devuelve true', () => {
    const slot = new CancelSlot();
    const run = slot.begin();

    expect(slot.finish(run)).toBe(true);
    // Cerrar no aborta: la operación terminó sola, no se la cortó a nadie.
    expect(run.aborted).toBe(false);
  });

  it('finish devuelve false para la respuesta de una operación vieja', () => {
    const slot = new CancelSlot();
    const vieja = slot.begin();
    slot.begin();

    expect(slot.finish(vieja)).toBe(false);
  });

  it('cancel aborta lo que hay en vuelo y corta el resto', () => {
    const slot = new CancelSlot();
    const run = slot.begin();

    expect(slot.cancel()).toBe(true);
    expect(run.aborted).toBe(true);
    // La ranura quedó libre: un clic de más no corta nada.
    expect(slot.cancel()).toBe(false);
  });

  it('la respuesta de lo cancelado no escribe nada', () => {
    const slot = new CancelSlot();
    const run = slot.begin();
    slot.cancel();

    // Es el caso que se ve en la pantalla: el `catch` de la promesa rechazada
    // llega después del click y no debe apagar el modal de la operación nueva.
    expect(slot.finish(run)).toBe(false);
  });
});

describe('isCancellation', () => {
  it('reconoce sólo el error de una cancelación', () => {
    expect(isCancellation(new RequestCancelled())).toBe(true);
    expect(isCancellation(new Error('El túnel no respondió'))).toBe(false);
    expect(isCancellation('texto')).toBe(false);
    expect(isCancellation(undefined)).toBe(false);
  });
});

describe('abortCtx', () => {
  it('sin señal no arma contexto: la petición queda como estaba', () => {
    expect(abortCtx(undefined)).toBeUndefined();
  });

  it('con señal la guarda en el contexto', () => {
    const signal = new AbortController().signal;
    expect(abortCtx(signal)?.get(ABORT)).toBe(signal);
  });
});

describe('abortInterceptor', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([abortInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  /** La misma forma que usa el cliente generado: una promesa por `firstValueFrom`. */
  const pedir = (url: string, context?: ReturnType<typeof abortCtx>) =>
    firstValueFrom(TestBed.inject(HttpClient).request('GET', url, { observe: 'response', context }));

  it('sin señal en el contexto deja pasar la respuesta', async () => {
    const respuesta = pedir('/api/envs');
    http.expectOne('/api/envs').flush({}, { status: 200, statusText: 'OK' });

    await expect(respuesta).resolves.toBeTruthy();
  });

  it('un abort() corta la suscripción y rechaza con RequestCancelled', async () => {
    const controller = new AbortController();
    const respuesta = pedir('/api/query', abortCtx(controller.signal));
    const enVuelo = http.expectOne('/api/query');

    controller.abort();

    // El `cancelled` es la otra mitad del aserto: sin abortar, la petición
    // seguiría abierta y el backend seguiría trabajando.
    expect(enVuelo.cancelled).toBe(true);
    await expect(respuesta).rejects.toBeInstanceOf(RequestCancelled);
  });

  it('una señal ya abortada al llegar no deja la promesa colgada', async () => {
    const controller = new AbortController();
    controller.abort();

    const respuesta = pedir('/api/query', abortCtx(controller.signal));

    // Es la carrera del doble click: el `abort()` ocurrió antes de que la
    // suscripción existiera, y un `addEventListener` a posteriori no dispara.
    expect(http.expectOne('/api/query').cancelled).toBe(true);
    await expect(respuesta).rejects.toBeInstanceOf(RequestCancelled);
  });
});