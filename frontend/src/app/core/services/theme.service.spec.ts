import { DOCUMENT } from '@angular/common';
import { TestBed } from '@angular/core/testing';
import { ThemeService } from './theme.service';

const THEME_STORAGE_KEY = 'yappy-region-sync-theme';

describe('ThemeService', () => {
  let storage: Map<string, string>;
  let attributes: Map<string, string>;

  beforeEach(() => {
    storage = new Map();
    attributes = new Map();

    const documentMock = {
      defaultView: {
        localStorage: {
          getItem: (key: string) => storage.get(key) ?? null,
          setItem: (key: string, value: string) => storage.set(key, value),
        },
      },
      documentElement: {
        setAttribute: (name: string, value: string) => attributes.set(name, value),
      },
    } as unknown as Document;

    TestBed.configureTestingModule({
      providers: [{ provide: DOCUMENT, useValue: documentMock }],
    });
  });

  it('starts in light mode when no preference is saved', () => {
    const theme = TestBed.inject(ThemeService);

    expect(theme.mode()).toBe('light');
    expect(attributes.get('data-theme')).toBe('light');
  });

  it('toggles and persists the selected mode', () => {
    const theme = TestBed.inject(ThemeService);

    theme.toggle();

    expect(theme.mode()).toBe('dark');
    expect(attributes.get('data-theme')).toBe('dark');
    expect(storage.get(THEME_STORAGE_KEY)).toBe('dark');
  });

  it('restores a saved dark preference', () => {
    storage.set(THEME_STORAGE_KEY, 'dark');

    const theme = TestBed.inject(ThemeService);

    expect(theme.isDark()).toBe(true);
    expect(attributes.get('data-theme')).toBe('dark');
  });
});
