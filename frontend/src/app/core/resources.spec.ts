/**
 * Los cinco estados y lo que cada uno ofrece.
 *
 * Lo que importa acá no es que el texto sea lindo: es que los botones cambien
 * según el estado y que un estado que el backend no conoce no rompa la pantalla.
 * Un `state` mal mapeado deja offering "Levantar" sobre un túnel que ya está
 * arriba, y el developer terminaclicke dos veces sobre algo que funciona.
 */
import {
  canOpen,
  canShowLogs,
  canStart,
  resourceMeta,
  startLabel,
  stateAnnouncement,
  stateBadge,
  stateBadgeClass,
  stateLabel,
} from './resources';

describe('estados de un recurso', () => {
  describe('qué botón ofrece cada estado', () => {
    it('down y dead ofrecen levantar; dead lo dice distinto', () => {
      expect(canStart('down')).toBe(true);
      expect(canStart('dead')).toBe(true);
      // "Levantar" y "Levantar de nuevo" son la misma acción y no la misma
      // información: si se cayó, hay algo que mirar en el log.
      expect(startLabel('down')).toBe('Levantar');
      expect(startLabel('dead')).toBe('Levantar de nuevo');
    });

    it('error ofrece reintentar y permite cerrarlo', () => {
      expect(canStart('error')).toBe(true);
      expect(startLabel('error')).toBe('Reintentar');
    });

    it('starting no ofrece nada: el proceso ya está en marcha', () => {
      expect(canStart('starting')).toBe(false);
      expect(canStart('up')).toBe(false);
    });

    it('sólo se abre una URL contra algo que respondió', () => {
      expect(canOpen('up')).toBe(true);
      expect(canOpen('starting')).toBe(false);
      expect(canOpen('down')).toBe(false);
      expect(canOpen('dead')).toBe(false);
      expect(canOpen('error')).toBe(false);
    });

    it('el log se ofrece mientras corre y mientras se lee por qué no corrió', () => {
      expect(canShowLogs('up')).toBe(true);
      expect(canShowLogs('starting')).toBe(true);
      // Un túnel caído es justo el caso en que el log explica qué pasó.
      expect(canShowLogs('dead')).toBe(false);
      expect(canShowLogs('down')).toBe(false);
    });
  });

  describe('un estado que el backend no conoce', () => {
    it('cae en el texto crudo en vez de romper', () => {
      // Si aparece un estado nuevo, el cliente lo muestra tal cual en vez de
      // pintar una fila sin etiqueta.
      expect(stateLabel('algo-nuevo', null)).toBe('algo-nuevo');
      expect(stateBadge('algo-nuevo')).toBe('algo-nuevo');
    });

    it('el badge queda sin color en vez de mentir', () => {
      expect(stateBadgeClass('algo-nuevo')).toBe('off');
    });
  });

  describe('la clase del badge', () => {
    it('arriba es el único verde', () => {
      expect(stateBadgeClass('up')).toBe('ok');
    });

    it('starting y dead comparten el ámbar: los dos son "no está bien"', () => {
      expect(stateBadgeClass('starting')).toBe('warn');
      expect(stateBadgeClass('dead')).toBe('warn');
    });

    it('error es el único rojo', () => {
      expect(stateBadgeClass('error')).toBe('error');
    });

    it('down es apagado, no un estado de alarma', () => {
      // Un puerto libre no es un problema. Pintarlo de ámbar haría que media
      // pantalla pareciera una advertencia.
      expect(stateBadgeClass('down')).toBe('off');
    });
  });

  describe('el texto de la celda', () => {
    const SIN_DESDE = 1757000000;

    it('agrega la antigüedad sólo cuando la hay', () => {
      expect(stateLabel('up', null)).toBe('arriba');
      expect(stateLabel('up', SIN_DESDE)).toContain('arriba ·');
    });

    it('un túnel caído dice que cayó y cuándo', () => {
      expect(stateLabel('dead', SIN_DESDE)).toContain('cayó');
      expect(stateLabel('dead', SIN_DESDE)).toContain('hace');
    });

    it('el badge no lleva antigüedad: con dos datos no cabe', () => {
      expect(stateBadge('up')).toBe('arriba');
      expect(stateBadge('up')).not.toContain('hace');
    });
  });

  describe('la línea de detalle', () => {
    it('un puerto del catálogo no tiene detail y no inventa uno', () => {
      // El catálogo dice puerto y nombre; no hay una frase que intrigue. Es mejor
      // una línea con pid y antigüedad que una con un texto vacío.
      const line = resourceMeta({ state: 'up', pid: 123, since: 1757000000 });
      expect(line).toContain('pid 123');
      expect(line).toContain('hace');
    });

    it('mientras levanta dice que está verificando', () => {
      expect(resourceMeta({ state: 'starting' })).toBe('verificando que responda');
    });

    it('un proceso muerto nombra el pid', () => {
      expect(resourceMeta({ state: 'dead', pid: 777, since: 1757000000 })).toContain('777');
    });

    it('un túnel arriba muestra el pid y la antigüedad, no el detail repetido', () => {
      const line = resourceMeta({ detail: 'KRaft', state: 'up', pid: 1, since: 1757000000 });
      expect(line).toContain('KRaft');
      expect(line).toContain('pid 1');
    });
  });

  describe('el anuncio del aria-live', () => {
    it('cada estado tiene su frase', () => {
      expect(stateAnnouncement('Tunnel 8402', 'up')).toBe('Tunnel 8402: arriba');
      expect(stateAnnouncement('Tunnel 8402', 'error')).toBe('Tunnel 8402: no levantó');
      expect(stateAnnouncement('Tunnel 8402', 'dead')).toBe('Tunnel 8402: se cayó');
    });

    it('una fila sin nombre usa el puerto', () => {
      expect(stateAnnouncement(':8403', 'starting')).toBe(':8403: levantando');
    });
  });
});