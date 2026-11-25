import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { Api } from '../../api-gen/api';
import {
  awsSessionLoginPoll,
  awsSessionLoginStart,
  awsSessionLogout,
  awsSessionStatus,
} from '../../api-gen/functions';
import type {
  AwsSessionLoginPollResponse,
  AwsSessionLoginStartResponse,
  AwsSessionStatusResponse,
} from '../../api-gen/models';
import { AwsSessionService } from './aws-session.service';

describe('AwsSessionService', () => {
  let status: AwsSessionStatusResponse;
  let statusAfterLogin: AwsSessionStatusResponse | null;
  let poll: AwsSessionLoginPollResponse;
  let pollError: Error | null;
  let statusCalls: number;
  let service: AwsSessionService | undefined;
  let open: typeof window.open;

  const loginStart: AwsSessionLoginStartResponse = {
    login_id: 'login-1',
    verification_uri: 'https://example.com/verify',
    verification_uri_complete: 'https://example.com/verify?user_code=ABC',
    user_code: 'ABC',
    expires_in: 600,
    profile: 'base-profile',
  };

  beforeEach(async () => {
    statusCalls = 0;
    statusAfterLogin = null;
    pollError = null;
    status = {
      active: true,
      profile: 'base-profile',
      expires_at: '2026-11-25T16:00:00Z',
      expires_in_seconds: 3600,
      message: null,
    };
    poll = {
      login_id: 'login-1',
      status: 'pending',
      active: false,
      profile: 'base-profile',
      expires_at: null,
      message: null,
    };

    const api = {
      invoke: async (fn: unknown) => {
        if (fn === awsSessionStatus) {
          statusCalls += 1;
          return statusAfterLogin ?? status;
        }
        if (fn === awsSessionLoginStart) return loginStart;
        if (fn === awsSessionLoginPoll) {
          if (pollError) throw pollError;
          return poll;
        }
        if (fn === awsSessionLogout) return { ok: true, message: 'Sesión cerrada.' };
        throw new Error('función de API inesperada');
      },
    };

    await TestBed.configureTestingModule({
      providers: [{ provide: Api, useValue: api }],
    }).compileComponents();

    open = window.open;
    window.open = vi.fn(() => null) as unknown as typeof window.open;
    service = TestBed.inject(AwsSessionService);
  });

  afterEach(() => {
    service?.dismissPrompt();
    window.open = open;
    vi.restoreAllMocks();
  });

  it('cuenta desde los segundos que mandó el backend, no desde expires_at', async () => {
    const now = Date.UTC(2026, 10, 25, 15, 0, 0);
    const clock = vi.spyOn(Date, 'now').mockReturnValue(now);
    const svc = service!;
    await svc.refreshStatus();

    expect(svc.statusFetchedAt()).toBe(now);
    expect(svc.remainingSeconds()).toBe(3600);

    clock.mockReturnValue(now + 13 * 60_000);
    svc.tick();

    expect(svc.remainingSeconds()).toBe(3600 - 13 * 60);
  });

  it('no muestra segundos de una sesión vencida aunque el status diga active', async () => {
    status = { ...status, active: false, expires_in_seconds: 0, message: 'Venció' };
    await service!.refreshStatus();

    expect(service!.remainingSeconds()).toBeNull();
  });

  it('volver a autorizar marca el modal como reautorización; un 401 no', async () => {
    const svc = service!;
    await svc.refreshStatus();
    await svc.startLogin();

    expect(svc.prompt()?.renew).toBe(true);

    svc.dismissPrompt();
    status = { ...status, active: false, expires_in_seconds: 0 };
    await svc.refreshStatus();
    svc.requestReauth({ message: 'venció' });

    expect(svc.prompt()?.renew).toBe(false);
  });

  it('logout limpia la UI y vuelve a consultar el estado', async () => {
    const svc = service!;
    await svc.refreshStatus();
    svc.prompt.set({ renew: true });
    statusAfterLogin = { active: false, profile: 'base-profile', expires_in_seconds: null, message: 'Sin sesión' };

    await svc.logout();

    expect(svc.prompt()).toBeNull();
    expect(svc.status()?.active).toBe(false);
    expect(svc.logoutError()).toBeNull();
  });

  it('un 404 en el polling corta el intento en vez de repetirlo cada minuto', async () => {
    const svc = service!;
    await svc.refreshStatus();
    await svc.startLogin();

    pollError = new HttpErrorResponse({ status: 404, error: { detail: 'No se encontró el intento de login.' } });
    await (svc as unknown as { poll: (id: string) => Promise<void> }).poll('login-1');

    expect(svc.loginBusy()).toBe(false);
    expect(svc.loginState()?.status).toBe('error');
    expect(svc.loginError()).toContain('se perdió');
    // Sin este stop, una pestaña de fondo sigue pegando 404 una vez por minuto.
    expect((svc as unknown as { pollTimer: unknown }).pollTimer).toBeNull();
  });

  it('al autorizar descarta el contador viejo y pide el estado real', async () => {
    const svc = service!;
    await svc.refreshStatus();
    await svc.startLogin();

    poll = {
      login_id: 'login-1',
      status: 'authorized',
      active: true,
      profile: 'base-profile',
      expires_at: '2026-11-25T17:14:00Z',
      message: 'Sesión autenticada',
    };
    statusAfterLogin = {
      active: true,
      profile: 'base-profile',
      expires_at: '2026-11-25T17:14:00Z',
      expires_in_seconds: 1200,
      message: null,
    };

    // `startPolling` ya disparó un poll pendiente; este segundo forza el
    // camino de autorización con el estado de arriba.
    await (svc as unknown as { poll: (id: string) => Promise<void> }).poll('login-1');

    expect(statusCalls).toBe(2);
    expect(svc.status()?.expires_in_seconds).toBe(1200);
    expect(svc.loginBusy()).toBe(false);
  });
});
