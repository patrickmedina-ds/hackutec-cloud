import { describe, expect, it } from 'vitest';
import { puedeTransicionar, TRANSICIONES, transicionesValidas } from './transiciones';
import { ESTADOS } from './types';

describe('transiciones (réplica de estados.py)', () => {
  it('replica exactamente el mapa del backend', () => {
    expect(TRANSICIONES).toEqual({
      REGISTRADA: ['EN_REVISION', 'CERRADA'],
      EN_REVISION: ['ATENDIDA', 'SOLUCIONADA', 'CERRADA'],
      ATENDIDA: ['EN_REVISION', 'SOLUCIONADA'],
      SOLUCIONADA: ['EN_REVISION', 'CERRADA'],
      CERRADA: [],
    });
  });
  it('cubre todos los estados', () => {
    expect(Object.keys(TRANSICIONES).sort()).toEqual([...ESTADOS].sort());
  });
  it('CERRADA es terminal', () => {
    expect(transicionesValidas('CERRADA')).toHaveLength(0);
  });
  it('valida pares', () => {
    expect(puedeTransicionar('REGISTRADA', 'EN_REVISION')).toBe(true);
    expect(puedeTransicionar('REGISTRADA', 'SOLUCIONADA')).toBe(false);
    expect(puedeTransicionar('ATENDIDA', 'CERRADA')).toBe(false);
    expect(puedeTransicionar('SOLUCIONADA', 'EN_REVISION')).toBe(true);
  });
});
