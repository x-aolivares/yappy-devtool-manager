import { signal, type WritableSignal } from '@angular/core';
import { TestBed, type ComponentFixture } from '@angular/core/testing';
import { provideRouter, type Route } from '@angular/router';
import { App } from './app';
import { routes } from './app.routes';
import { AwsSessionService } from './core/services/aws-session.service';
import type { AwsSessionPrompt } from './core/services/aws-session.service';
import type { AwsSessionStatusResponse } from './api-gen/models';
import { ThemeService } from './core/services/theme.service';

describe('App', () => {
  beforeEach(async () => {
    const isDark = signal(false);
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter([]),
        {
          provide: ThemeService,
          useValue: {
            isDark,
            toggle: () => isDark.update((dark) => !dark),
          },
        },
      ],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });

  it('should render the brand', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.brand')?.textContent).toContain('Yappy DevTool');
  });

  it('exposes an accessible control to switch theme modes', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    const toggle = compiled.querySelector<HTMLButtonElement>('.theme-toggle');

    expect(toggle?.getAttribute('aria-label')).toBe('Alternar modo oscuro');
    expect(toggle?.getAttribute('aria-pressed')).toBe('false');

    toggle?.click();
    fixture.detectChanges();

    expect(toggle?.getAttribute('aria-pressed')).toBe('true');
    expect(toggle?.textContent).toContain('Modo claro');
  });

  it('groups the navigation under one heading per section', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const compiled = fixture.nativeElement as HTMLElement;

    const labels = [...compiled.querySelectorAll('.section-label')].map((el) =>
      el.textContent?.trim(),
    );
    // El orden importa y es el de NAV_SECTIONS: Workflow va entre Database y
    // Bitbucket, porque es lo que se usa para levantar y parar cosas, y eso pasa
    // antes de ir a mirar un despliegue.
    expect(labels).toEqual(['AWS', 'Database', 'Workflow', 'Bitbucket', 'CircleCI']);
  });

  it('keeps Inicio outside any section and points at the root route', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const compiled = fixture.nativeElement as HTMLElement;

    const home = compiled.querySelector<HTMLAnchorElement>('.nav .home');
    expect(home?.textContent?.trim()).toBe('Inicio');
    expect(home?.getAttribute('href')).toBe('/');
    // Nothing is highlighted until a route is active: Inicio is not inside a section.
    expect(compiled.querySelector('.section .home')).toBeNull();
  });

  it('marks sections without tools instead of hiding them', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const compiled = fixture.nativeElement as HTMLElement;

    // CircleCI es la única sección sin herramientas: Bitbucket ya tiene "Rama
    // desplegada", así que su `emptyNote` se fue con ella.
    const empty = [...compiled.querySelectorAll('.section-empty')].map((el) =>
      el.textContent?.trim(),
    );
    expect(empty).toEqual(['Sin herramientas todavía']);
  });

  it('links every AWS, Database, Workflow and Bitbucket tool to a real route', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const compiled = fixture.nativeElement as HTMLElement;

    const hrefs = [...compiled.querySelectorAll('.section a')].map((el) => el.getAttribute('href'));
    // AWS quedó con una sola herramienta: Diff, Crear, Editar y Sesiones salen
    // del menú. `/sessions` sigue enrutada aunque no esté acá, porque la página
    // de leer parámetros enlaza a esa vista.
    // El orden importa y es el de NAV_SECTIONS, no alfabético.
    //
    // Los prefijos `/aws` y `/db` son reales: las rutas se agruparon bajo ellos y
    // este test seguía comparando contra las cortas. Fallaba desde ese refactor.
    //
    // Workflow aporta cuatro ítems y no uno: la pantalla era una sola con cuatro
    // secciones apiladas y cada sección tiene su ruta ahora.
    expect(hrefs).toEqual([
      '/aws/params-read',
      '/db/sql',
      '/db/migrate-data',
      '/db/compile',
      '/db/schema-sync',
      '/workflow/kafka',
      '/workflow/tunnels',
      '/workflow/services',
      '/workflow/database',
      '/deployments',
    ]);
  });

  it('no deja en el menú ninguna ruta que ya no exista', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const compiled = fixture.nativeElement as HTMLElement;

    // La pantalla `/workflow/resources` se partió en cuatro. El redirect está en
    // `app.routes.ts` para los links viejos, pero el menú tiene que apuntar a las
    // cuatro rutas nuevas: si alguna se olvidara, el ítem caería en el `**` y
    // devolvería al home sin avisar.
    const hrefs = [...compiled.querySelectorAll('.section a')].map((el) => el.getAttribute('href'));

    expect(hrefs).not.toContain('/workflow/resources');
    // `aws` y `db` son prefijos con hijos, así que no alcanza con leer `path`:
    // hay que armar la ruta completa de cada hoja.
    const full = new Set<string>();
    const walk = (rs: Route[], prefix = ''): void => {
      for (const r of rs) {
        const path = `${prefix}/${r.path}`;
        if (r.children?.length) walk(r.children, path);
        else full.add(path);
      }
    };
    walk(routes);

    for (const href of hrefs) {
      expect(full.has(href!)).toBe(true);
    }
  });
});

describe('App — sesión AWS en el sidebar', () => {
  type SessionStatus = 'pending' | 'authorized' | 'expired' | 'error';
  type FakeAwsSession = {
    status: WritableSignal<AwsSessionStatusResponse | null>;
    prompt: WritableSignal<AwsSessionPrompt | null>;
    remainingSeconds: WritableSignal<number | null>;
    loginBusy: WritableSignal<boolean>;
    loginState: WritableSignal<{ status: SessionStatus } | null>;
    login: WritableSignal<unknown>;
    loginError: WritableSignal<string | null>;
    logoutError: WritableSignal<string | null>;
    statusFetchedAt: WritableSignal<number | null>;
    refreshStatus: () => Promise<null>;
    tick: () => void;
    startLogin: () => Promise<void>;
    logout: () => Promise<void>;
    dismissPrompt: () => void;
  };
  let status: WritableSignal<AwsSessionStatusResponse | null>;
  let prompt: WritableSignal<AwsSessionPrompt | null>;
  let remaining: WritableSignal<number | null>;
  let loginBusy: WritableSignal<boolean>;
  let loginState: WritableSignal<{ status: SessionStatus } | null>;
  let fake: FakeAwsSession;
  let fixture: ComponentFixture<App>;

  beforeEach(async () => {
    status = signal<AwsSessionStatusResponse | null>(null);
    prompt = signal<AwsSessionPrompt | null>(null);
    remaining = signal<number | null>(null);
    loginBusy = signal(false);
    loginState = signal<{ status: SessionStatus } | null>(null);

    fake = {
      status,
      prompt,
      remainingSeconds: remaining,
      loginBusy,
      loginState,
      login: signal<unknown>(null),
      loginError: signal<string | null>(null),
      logoutError: signal<string | null>(null),
      statusFetchedAt: signal<number | null>(null),
      refreshStatus: async () => null,
      tick: () => {},
      logout: async () => {},
      startLogin: async () => {
        if (loginBusy()) return;
        loginBusy.set(true);
        prompt.set({ profile: status()?.profile ?? null, message: null, renew: !!status()?.active });
      },
      dismissPrompt: () => {
        prompt.set(null);
        loginBusy.set(false);
        loginState.set(null);
      },
    };

    const isDark = signal(false);
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter([]),
        { provide: ThemeService, useValue: { isDark, toggle: () => isDark.update((d) => !d) } },
        { provide: AwsSessionService, useValue: fake },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(App);
    fixture.detectChanges();
  });

  function label(): string | undefined {
    return (fixture.nativeElement as HTMLElement)
      .querySelector('.aws-session-status__row span:last-child')
      ?.textContent?.trim();
  }

  function meta(): string | undefined {
    return (fixture.nativeElement as HTMLElement)
      .querySelector('.aws-session-status__meta')
      ?.textContent?.trim();
  }

  it('muestra Activa mientras sobra tiempo y pasa a Por vencer con 15 min o menos', () => {
    status.set({
      active: true,
      profile: 'base-profile',
      expires_at: '2026-11-25T16:00:00Z',
      expires_in_seconds: 1200,
    });
    remaining.set(1200);
    fixture.detectChanges();

    expect(label()).toBe('Activa');
    expect(meta()).toContain('Sesión SSO vence:');
    expect(meta()).toContain('en ~20 min');
    const dot = (fixture.nativeElement as HTMLElement).querySelector('.aws-session-status__dot')!;
    expect(dot.classList.contains('aws-session-status__dot--ok')).toBe(true);

    remaining.set(800);
    fixture.detectChanges();

    expect(label()).toBe('Por vencer');
    expect(meta()).toContain('en ~14 min');
    expect(dot.classList.contains('aws-session-status__dot--warn')).toBe(true);
  });

  it('cae a Sin autenticar al llegar a cero y muestra el mensaje del backend', () => {
    status.set({
      active: false,
      profile: 'base-profile',
      expires_in_seconds: 0,
      message: 'La sesión de AWS venció o no está activa.',
    });
    remaining.set(0);
    fixture.detectChanges();

    expect(label()).toBe('Sin autenticar');
    expect(meta()).toContain('La sesión de AWS venció');
    const dot = (fixture.nativeElement as HTMLElement).querySelector('.aws-session-status__dot')!;
    expect(dot.classList.contains('aws-session-status__dot--danger')).toBe(true);
  });

  it('el modal dice Volver a autorizar cuando la sesión está activa y Iniciar cuando no', () => {
    status.set({
      active: true,
      profile: 'base-profile',
      expires_at: '2026-11-25T21:55:00Z',
      expires_in_seconds: 3600,
    });
    remaining.set(3600);
    prompt.set({ renew: true, profile: 'base-profile' });
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.notice-modal h2')?.textContent?.trim()).toBe('Volver a autorizar sesión AWS');
    expect(compiled.querySelector('.notice-modal__confirm')?.textContent?.trim()).toBe('Volver a autorizar');
    expect(compiled.querySelector('.aws-session-status__action')?.textContent?.trim()).toBe('Volver a autorizar');
    expect(compiled.querySelector('.notice-modal__body')?.textContent).toContain('no cambia esa hora');

    prompt.set({ renew: false });
    fixture.detectChanges();

    expect(compiled.querySelector('.notice-modal h2')?.textContent?.trim()).toBe('Sesión de AWS no autenticada');
    expect(compiled.querySelector('.notice-modal__confirm')?.textContent?.trim()).toBe('Iniciar sesión AWS');
  });

  it('ofrece cerrar sesión sólo cuando hay una sesión activa', () => {
    const actions = () => [...(fixture.nativeElement as HTMLElement).querySelectorAll('.aws-session-status__action')];
    expect(actions().map((b) => b.textContent?.trim())).toEqual(['Iniciar sesión AWS']);

    status.set({ active: true, profile: 'base-profile', expires_in_seconds: 3600 });
    fixture.detectChanges();

    expect(actions().map((b) => b.textContent?.trim())).toEqual(['Volver a autorizar', 'Cerrar sesión AWS']);
  });

  it('el botón dice que espera autorización mientras hay un login en curso', () => {
    status.set({ active: true, profile: 'base-profile', expires_in_seconds: 3600 });
    loginBusy.set(true);
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('.aws-session-status__action')?.textContent?.trim()).toBe(
      'Esperando autorización…',
    );
  });

  it('al volver la pestaña refresca el estado en vez de usar el timer suspendido', () => {
    const refresh = vi.spyOn(fake, 'refreshStatus');
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
    refresh.mockClear();

    document.dispatchEvent(new Event('visibilitychange'));

    expect(refresh).toHaveBeenCalledTimes(1);
    visibility.mockRestore();
  });
});
