import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, type Route } from '@angular/router';
import { App } from './app';
import { routes } from './app.routes';
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
