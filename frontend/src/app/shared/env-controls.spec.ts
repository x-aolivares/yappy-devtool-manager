import { ComponentFixture, TestBed } from '@angular/core/testing';
import { EnvironmentInfo } from '../api-gen/models';
import { EnvControlsComponent, PARAM_SERVICES } from './env-controls';

const ENVS: EnvironmentInfo[] = [
  { env: 'dev', region: 'us-west-2', profile: 'localstack' },
  { env: 'qa', region: 'us-west-1', profile: 'localstack' },
  { env: 'local', region: 'us-west-2', profile: 'local-profile' },
];

async function setup(inputs: Record<string, unknown> = {}) {
  await TestBed.configureTestingModule({ imports: [EnvControlsComponent] }).compileComponents();
  const fixture: ComponentFixture<EnvControlsComponent> =
    TestBed.createComponent(EnvControlsComponent);
  fixture.componentRef.setInput('environments', ENVS);
  for (const [key, value] of Object.entries(inputs)) {
    fixture.componentRef.setInput(key, value);
  }
  fixture.detectChanges();
  return fixture;
}

function groups(fixture: ComponentFixture<EnvControlsComponent>): string[] {
  return [...fixture.nativeElement.querySelectorAll('.pill-label')].map(
    (el) => (el as HTMLElement).textContent?.trim() ?? '',
  );
}

function envButtons(fixture: ComponentFixture<EnvControlsComponent>): HTMLButtonElement[] {
  return [...fixture.nativeElement.querySelectorAll('.env-item')] as HTMLButtonElement[];
}

describe('EnvControlsComponent', () => {
  it('labels the environments group and renders one pill per environment', async () => {
    const fixture = await setup();

    expect(groups(fixture)).toEqual(['Ambientes']);
    expect(envButtons(fixture).length).toBe(3);
  });

  it('uses the given label for the environments group', async () => {
    const fixture = await setup({ envLabel: 'Regiones destino' });

    expect(groups(fixture)).toEqual(['Regiones destino']);
  });

  it('does not draw the services group when there are no options', async () => {
    const fixture = await setup();

    expect(groups(fixture)).not.toContain('Servicios');
  });

  it('draws the services group only when options are given', async () => {
    const fixture = await setup({ services: PARAM_SERVICES });

    expect(groups(fixture)).toEqual(['Servicios', 'Ambientes']);
    const chips = fixture.nativeElement.querySelectorAll('.chip') as NodeListOf<HTMLElement>;
    expect([...chips].map((c) => c.textContent?.replace('✓', '').trim())).toEqual([
      'SSM Parameter Store',
      'Secrets Manager',
    ]);
  });

  it('keeps selection order so callers can derive origen/destino', async () => {
    const fixture = await setup({ max: 2 });
    const [, qa, local] = envButtons(fixture);

    local.click();
    qa.click();
    fixture.detectChanges();

    expect(fixture.componentInstance.envs()).toEqual(['local', 'qa']);
  });

  it('exposes the single environment when max is 1', async () => {
    const fixture = await setup({ max: 1 });
    envButtons(fixture)[1].click();
    fixture.detectChanges();

    expect(fixture.componentInstance.single()).toBe('qa');

    envButtons(fixture)[1].click();
    fixture.detectChanges();

    expect(fixture.componentInstance.single()).toBe('');
  });

  it('marks the active service with a check and aria-pressed', async () => {
    const fixture = await setup({ services: PARAM_SERVICES, service: 'secretsmanager' });
    const chips = [...fixture.nativeElement.querySelectorAll('.chip')] as HTMLButtonElement[];

    expect(chips.map((c) => c.getAttribute('aria-pressed'))).toEqual(['false', 'true']);
    expect(chips[1].querySelector('[aria-hidden]')?.textContent?.trim()).toBe('✓');
  });

  it('caps the environments at max', async () => {
    const fixture = await setup({ max: 2 });
    const [dev, qa, local] = envButtons(fixture);

    dev.click();
    qa.click();
    fixture.detectChanges();

    expect(local.disabled).toBe(true);
    expect(fixture.componentInstance.envs()).toEqual(['dev', 'qa']);
  });
});