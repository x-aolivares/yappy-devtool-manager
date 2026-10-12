import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ParamsDiffPage } from './params-diff';
import { ParamsService } from '../core/services/params.service';
import { EnvironmentService } from '../core/services/environment.service';
import { SessionService } from '../core/services/session.service';

// jshint ignore:start
const PAIR: Record<string, any> = {
  status: 'different',
  pair: true,
  env_a: 'qa',
  env_b: 'dev',
  service: 'ssm',
  name: '/prod/ecommerce/db/master_url',
  value_a: 'alias-secret-qa',
  value_b: 'alias-secret-dev',
  secret_value_a: 'prrito$14',
  secret_value_b: 'prrito$2026',
  param_status: 'different',
  secret_status: 'different',
  param_needs_write: true,
  secret_needs_write: true,
  param_apply: 'alias-secret-dev',
  secret_apply: 'prrito$2026',
  notes: [],
  changes: [],
  is_json: false,
};

describe('ParamsDiffPage pair mode', () => {
  let mockDiff: () => Promise<Record<string, any>>;

  beforeEach(async () => {
    mockDiff = () => Promise.resolve(PAIR);
    await TestBed.configureTestingModule({
      imports: [ParamsDiffPage],
      providers: [
        provideRouter([]),
        {
          provide: EnvironmentService,
          useValue: { list: () => Promise.resolve({ environments: [] }) },
        },
        {
          provide: ParamsService,
          useValue: {
            diff: () => mockDiff(),
            apply: () => Promise.resolve({ script: 'aws ...' }),
            applyExecute: () => Promise.resolve({ steps: [] }),
          },
        },
        {
          provide: SessionService,
          useValue: {
            updateItem: () => Promise.resolve({}),
          },
        },
      ],
    }).compileComponents();
  });

  async function render(checkSecret: boolean) {
    const fixture = TestBed.createComponent(ParamsDiffPage);
    const comp = fixture.componentInstance as any;
    comp.envA.set('qa');
    comp.envB.set('dev');
    comp.name.set('/prod/ecommerce/db/master_url');
    comp.withSecret.set(checkSecret);
    comp.compare();
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return { fixture, comp, el: fixture.nativeElement as HTMLElement };
  }

  it('renders the editable secret table when the pair has writes', async () => {
    const { comp, el } = await render(true);
    expect(comp.data()?.pair).toBe(true);
    expect(comp.mode()).toBe('pair');
    expect(el.querySelectorAll('.pair-input').length).toBeGreaterThan(0);
    expect(el.textContent).toContain('Secreto (Secrets Manager)');
  });

  it('shows the editable secret even without a secret write when the checkbox is checked', async () => {
    mockDiff = () =>
      Promise.resolve({
        ...PAIR,
        secret_needs_write: false,
        secret_status: 'different',
        param_needs_write: false,
        param_status: 'different',
      });
    const { comp, el } = await render(true);
    expect(comp.mode()).toBe('pair');
    const textareas = Array.from(el.querySelectorAll('.pair-input')) as HTMLTextAreaElement[];
    expect(textareas.length).toBeGreaterThan(0);
    const secretRows = Array.from(el.querySelectorAll('tbody tr')).filter((r) =>
      r.textContent.includes('Secreto (Secrets Manager)'),
    );
    expect(secretRows.length).toBe(1);
  });

  it('does NOT show a secret-only row when the checkbox is unchecked and no secret write is needed', async () => {
    mockDiff = () =>
      Promise.resolve({
        ...PAIR,
        secret_needs_write: false,
        param_needs_write: false,
      });
    const { comp, el } = await render(false);
    expect(comp.mode()).toBe('pair');
    expect(el.querySelectorAll('.pair-input').length).toBe(0);
    expect(el.textContent).not.toContain('Secreto (Secrets Manager)');
  });
});



describe('ParamsDiffPage — espera como modal', () => {
  let fixture: ComponentFixture<ParamsDiffPage>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [ParamsDiffPage] }).compileComponents();
    fixture = TestBed.createComponent(ParamsDiffPage);
  });

  const host = (): HTMLElement => fixture.nativeElement as HTMLElement;

  it('no abre el modal de espera en reposo', () => {
    fixture.detectChanges();
    expect(host().querySelector('.busy-modal')).toBeNull();
  });

  it('abre el modal de espera mientras la página está busy', async () => {
    (fixture.componentInstance as unknown as { busy: { set(v: boolean): void } }).busy.set(true);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(host().querySelector('.busy-modal')).not.toBeNull();
    expect(host().querySelector('.busy-modal__backdrop')).not.toBeNull();
  });

  it('cierra el modal de espera cuando deja de estar busy', async () => {
    const c = fixture.componentInstance as unknown as {
      busy: { set(v: boolean): void };
    };
    c.busy.set(true);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(host().querySelector('.busy-modal')).not.toBeNull();

    c.busy.set(false);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(host().querySelector('.busy-modal')).toBeNull();
  });
});
