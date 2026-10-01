import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { App } from './app';
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
});
