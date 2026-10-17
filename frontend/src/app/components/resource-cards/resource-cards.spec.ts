/**
 * La grilla de tarjetas que comparten Kafka, Servicios y Base de datos.
 *
 * Lo que se verifica acá es qué botones **no** se dibujan, más que los que sí.
 * Cada botón ausente es una barrera entre un clic y algo que el backend va a
 * rechazar —o, peor, que va a matar y no le importa de quién era.
 */
import { TestBed } from '@angular/core/testing';

import { ResourceCards } from './resource-cards';
import { recurso } from '../../core/workflow-doubles';
import type { WorkflowResourceInfo } from '../../api-gen/models';

describe('ResourceCards', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [ResourceCards] }).compileComponents();
  });

  function montar(rows: readonly WorkflowResourceInfo[], portMode: 'local' | 'remote' = 'local'): HTMLElement {
    const fixture = TestBed.createComponent(ResourceCards);
    fixture.componentRef.setInput('rows', rows);
    fixture.componentRef.setInput('portMode', portMode);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  /** La tarjeta de una fila, buscada por el nombre del recurso. */
  function tarjeta(rows: readonly WorkflowResourceInfo[], nombre: string): HTMLElement {
    const el = montar(rows);
    const card = [...el.querySelectorAll<HTMLElement>('.res-card')].find((c) =>
      c.querySelector('.res-card__name')?.textContent?.includes(nombre),
    );
    if (!card) throw new Error(`No está la tarjeta de ${nombre}`);
    return card;
  }

  function botones(card: HTMLElement): string[] {
    return [...card.querySelectorAll('button')].map((b) => b.textContent?.trim() ?? '');
  }

  // --- qué ofrece cada estado ----------------------------------------------

  describe('qué ofrece cada estado', () => {
    it('abajo ofrece levantar y nada más', () => {
      expect(botones(tarjeta([recurso()], 'Bastion'))).toEqual(['Levantar']);
    });

    it('levantándose NO ofrece levantar de nuevo', () => {
      // El proceso ya está en marcha. Un segundo click abre otro start-session
      // sobre un puerto que ya está bindeado.
      const labels = botones(tarjeta([recurso({ state: 'starting' })], 'Bastion'));
      expect(labels).toContain('Levantando…');
      expect(labels.filter((l) => l === 'Levantar')).toHaveLength(0);
    });

    it('arriba ofrece parar y abrir, y no levantar', () => {
      const card = tarjeta(
        [recurso({ state: 'up', pid: 4242, browser_url: 'https://localhost:3000/swagger-ui/index.html' })],
        'Bastion',
      );
      expect(botones(card)).toContain('Parar');
      expect(botones(card).filter((l) => l === 'Levantar')).toHaveLength(0);
      expect(card.querySelector('a[href*="swagger-ui"]')).not.toBeNull();
    });

    it('caída dice "Levantar de nuevo", no "Levantar"', () => {
      const labels = botones(tarjeta([recurso({ state: 'dead' })], 'Bastion'));
      expect(labels).toContain('Levantar de nuevo');
      expect(labels).not.toContain('Levantar');
    });

    it('en error ofrece reintentar', () => {
      expect(botones(tarjeta([recurso({ state: 'error' })], 'Bastion'))).toContain('Reintentar');
    });

    it('arriba pero sin pid no ofrece ver logs', () => {
      // Sin pid no hay archivo de log que leer: ofrecer el botón abriría un panel
      // que no puede pedir nada.
      expect(botones(tarjeta([recurso({ state: 'up', pid: null })], 'Bastion'))).not.toContain('Ver logs');
    });
  });

  // --- los procesos ajenos --------------------------------------------------

  describe('los procesos ajenos', () => {
    it('llevan borde punteado y la etiqueta "ajeno"', () => {
      const card = tarjeta([recurso({ foreign: true, state: 'up', pid: 99 })], 'Bastion');
      expect(card.classList).toContain('res-card--foreign');
      expect(card.textContent).toContain('ajeno');
    });

    it('NO tienen botón de parar', () => {
      // El botón de una fila nunca alcanza un proceso que no es de la web: es la
      // única barrera entre un clic y el túnel de otro proyecto.
      expect(botones(tarjeta([recurso({ foreign: true, state: 'up', pid: 99 })], 'Bastion'))).not.toContain('Parar');
    });

    it('abajo no lleva la etiqueta "ajeno"', () => {
      // `foreign` con `down` es el estado por defecto de una fila: nadie lo
      // levantó. Marcarlo como ajeno sería mentir sobre lo que hay.
      const card = tarjeta([recurso({ foreign: true, state: 'down' })], 'Bastion');
      expect(card.classList).not.toContain('res-card--foreign');
      expect(card.textContent).not.toContain('ajeno');
    });
  });

  // --- el puerto ------------------------------------------------------------

  describe('el puerto que muestra la tarjeta', () => {
    it('en `local` muestra sólo el puerto local', () => {
      const card = tarjeta([recurso({ local_port: 3000, remote_port: 9091 })], 'Bastion');
      expect(card.querySelector('.res-card__port')?.textContent).toBe(':3000');
    });

    it('en `remote` muestra los dos, porque el servicio traduce', () => {
      // Un túnel a un servicio con destino propio mapea uno en otro, y callar el
      // puerto de destino esconde justo el dato que explica el row.
      const el = montar([recurso({ local_port: 3000, remote_port: 9091 })], 'remote');
      expect(el.querySelector('.res-card__port')?.textContent).toBe(':9091 → :3000');
    });

    it('en `remote` cae al local si la fila no tiene puerto remoto', () => {
      const el = montar([recurso({ local_port: 3000, remote_port: null })], 'remote');
      expect(el.querySelector('.res-card__port')?.textContent).toBe(':3000');
    });
  });

  // --- el conflicto de puerto -----------------------------------------------

  it('una fila con conflicto lo dice en la tarjeta', () => {
    const card = tarjeta([recurso({ conflict: 'el cluster cap2' })], 'Bastion');
    expect(card.textContent).toContain('ya lo está usando el cluster cap2');
    expect(card.classList).toContain('res-card--conflict');
  });

  // --- el vacío -------------------------------------------------------------

  it('sin filas dice lo que le pasó la página', () => {
    const fixture = TestBed.createComponent(ResourceCards);
    fixture.componentRef.setInput('rows', []);
    fixture.componentRef.setInput('emptyText', 'No hay Kafka corriendo en este ambiente.');
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain('No hay Kafka corriendo');
  });

  // --- las acciones ---------------------------------------------------------

  describe('lo que emite', () => {
    it('cada botón emite su fila, no la llama', () => {
      // El componente no habla con el backend: emite y lo decide la página. Es lo
      // que permite que el `busy` sea de la página y no del componente.
      const fixture = TestBed.createComponent(ResourceCards);
      const row = recurso({ state: 'up', pid: 77 });
      fixture.componentRef.setInput('rows', [row]);
      fixture.detectChanges();

      const emitido: unknown[] = [];
      fixture.componentInstance.stop.subscribe((r) => emitido.push(r));
      fixture.componentInstance.start.subscribe((r) => emitido.push(r));
      fixture.componentInstance.logs.subscribe((r) => emitido.push(r));

      const botones = [...(fixture.nativeElement as HTMLElement).querySelectorAll('button')];
      const porTexto = (t: string) => botones.find((b) => b.textContent?.trim() === t);
      porTexto('Parar')?.click();
      porTexto('Ver logs')?.click();

      expect(emitido).toEqual([row, row]);
    });

    it('los botones se deshabilitan mientras la página está ocupada', () => {
      const fixture = TestBed.createComponent(ResourceCards);
      fixture.componentRef.setInput('rows', [recurso({ state: 'up', pid: 77 })]);
      fixture.componentRef.setInput('busy', true);
      fixture.detectChanges();

      const botones = [...(fixture.nativeElement as HTMLElement).querySelectorAll('button')];
      expect(botones.filter((b) => b.textContent?.trim() !== 'Ver logs').every((b) => b.disabled)).toBe(true);
    });
  });
});
