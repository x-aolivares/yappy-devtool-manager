import { ComponentFixture, TestBed } from '@angular/core/testing';
import { EnvironmentInfo } from '../api-gen/models';
import { awsEnvironments } from '../core/format';
import { EnvPicker } from './env-picker';

const ENVS: EnvironmentInfo[] = [
  { env: 'dev', region: 'us-west-2', profile: 'localstack' },
  { env: 'qa', region: 'us-west-1', profile: 'localstack' },
  { env: 'sandbox', region: 'us-east-1', profile: 'base-profile' },
];

async function setup(max = 0, selected: string[] = []) {
  await TestBed.configureTestingModule({ imports: [EnvPicker] }).compileComponents();
  const fixture: ComponentFixture<EnvPicker> = TestBed.createComponent(EnvPicker);
  fixture.componentRef.setInput('environments', ENVS);
  fixture.componentRef.setInput('max', max);
  fixture.componentRef.setInput('selected', selected);
  fixture.detectChanges();
  return fixture;
}

function buttons(fixture: ComponentFixture<EnvPicker>): HTMLButtonElement[] {
  return [...fixture.nativeElement.querySelectorAll('.env-item')] as HTMLButtonElement[];
}

describe('EnvPicker', () => {
  it('renders one toggle pill per environment, with no select and no checkbox', async () => {
    const fixture = await setup();
    const el = fixture.nativeElement as HTMLElement;

    expect(buttons(fixture).length).toBe(3);
    expect(el.querySelector('select')).toBeNull();
    expect(el.querySelector('input[type="checkbox"]')).toBeNull();
  });

  it('shows a check mark only on the selected environments', async () => {
    const fixture = await setup(0, ['qa']);

    const marks = buttons(fixture).map((b) => b.querySelector('.env-check')?.textContent?.trim());
    expect(marks).toEqual(['', '✓', '']);
  });

  it('marks selection for assistive tech with aria-pressed', async () => {
    const fixture = await setup(0, ['dev', 'sandbox']);

    expect(buttons(fixture).map((b) => b.getAttribute('aria-pressed'))).toEqual([
      'true',
      'false',
      'true',
    ]);
  });

  it('selects and deselects on click', async () => {
    const fixture = await setup();
    const [dev, qa] = buttons(fixture);

    qa.click();
    fixture.detectChanges();
    expect(fixture.componentInstance.selected()).toEqual(['qa']);

    dev.click();
    fixture.detectChanges();
    expect(fixture.componentInstance.selected()).toEqual(['qa', 'dev']);

    qa.click();
    fixture.detectChanges();
    expect(fixture.componentInstance.selected()).toEqual(['dev']);
  });

  it('keeps selection order so callers can derive origen/destino', async () => {
    const fixture = await setup(2);
    const [, qa, sandbox] = buttons(fixture);

    sandbox.click();
    qa.click();
    fixture.detectChanges();

    expect(fixture.componentInstance.selected()).toEqual(['sandbox', 'qa']);
  });

  it('caps selection at max and blocks new picks without dropping the current ones', async () => {
    const fixture = await setup(1, ['dev']);
    const [dev, qa] = buttons(fixture);

    expect(qa.disabled).toBe(true);
    qa.click();
    fixture.detectChanges();
    expect(fixture.componentInstance.selected()).toEqual(['dev']);

    // The selected one stays enabled so it can be turned off.
    expect(dev.disabled).toBe(false);
    dev.click();
    fixture.detectChanges();
    expect(fixture.componentInstance.selected()).toEqual([]);
  });

  it('does not cap selection when max is 0', async () => {
    const fixture = await setup(0);

    for (const b of buttons(fixture)) {
      b.click();
    }
    fixture.detectChanges();

    expect(fixture.componentInstance.selected()).toEqual(['dev', 'qa', 'sandbox']);
  });

  it('labels the slot each environment occupies when roleLabels is given', async () => {
    const fixture = await setup(2, ['qa', 'sandbox']);
    fixture.componentRef.setInput('roleLabels', ['Origen', 'Destino']);
    fixture.detectChanges();

    const roles = buttons(fixture).map((b) => b.querySelector('.env-role')?.textContent?.trim());
    expect(roles).toEqual([undefined, 'Origen', 'Destino']);
  });

  it('shows no role when none is given', async () => {
    const fixture = await setup(0, ['qa']);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.env-role')).toBeNull();
  });

  it('reports a missing environment list instead of rendering empty panels', async () => {
    const fixture = await setup();
    fixture.componentRef.setInput('environments', []);
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.env-item')).toBeNull();
    expect(el.textContent).toContain('No se encontraron ambientes');
  });

  it('does not show a region or profile for a local environment', async () => {
    // Un ambiente local no tiene región ni profile: hereda `us-west-2` y
    // `base-profile` de env.base, que son defaults con forma de AWS. Mostrarlos
    // sería inventar datos, y `base-profile` existe de verdad en la máquina.
    await TestBed.configureTestingModule({ imports: [EnvPicker] }).compileComponents();
    const fixture = TestBed.createComponent(EnvPicker);
    fixture.componentRef.setInput('environments', [{ env: 'local', is_local: true }]);
    fixture.detectChanges();

    const meta = fixture.nativeElement.querySelector('.env-meta')?.textContent?.trim();
    expect(meta).toBe('local · base de datos directa');
    expect(meta).not.toContain('base-profile');
    expect(meta).not.toContain('us-west-2');
  });

  it('still shows region and profile for a normal environment', async () => {
    const fixture = await setup();

    const metas = buttons(fixture).map((b) => b.querySelector('.env-meta')?.textContent?.trim());
    expect(metas).toEqual([
      'us-west-2 · localstack',
      'us-west-1 · localstack',
      'us-east-1 · base-profile',
    ]);
  });
});

describe('awsEnvironments', () => {
  it('drops local environments and keeps the rest in order', () => {
    const list: EnvironmentInfo[] = [
      { env: 'dev', region: 'us-west-2', profile: 'localstack' },
      { env: 'local', is_local: true },
      { env: 'qa', region: 'us-west-1', profile: 'localstack' },
    ];

    expect((awsEnvironments(list) ?? []).map((e) => e.env)).toEqual(['dev', 'qa']);
  });

  it('passes null and undefined through instead of crashing', () => {
    expect(awsEnvironments(null)).toBeNull();
    expect(awsEnvironments(undefined)).toBeNull();
  });

  it('keeps environments whose flag is absent, since that means "not local"', () => {
    const list: EnvironmentInfo[] = [{ env: 'dev', region: 'us-west-2' }];

    expect((awsEnvironments(list) ?? []).map((e) => e.env)).toEqual(['dev']);
  });
});
