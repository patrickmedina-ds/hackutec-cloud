import type { Estado } from './types';

/** Réplica de TRANSICIONES en aws/layers/comun/python/telecorp_comun/estados.py */
export const TRANSICIONES: Record<Estado, readonly Estado[]> = {
  REGISTRADA: ['EN_REVISION', 'CERRADA'],
  EN_REVISION: ['ATENDIDA', 'SOLUCIONADA', 'CERRADA'],
  ATENDIDA: ['EN_REVISION', 'SOLUCIONADA'],
  SOLUCIONADA: ['EN_REVISION', 'CERRADA'],
  CERRADA: [],
};

export function transicionesValidas(actual: Estado): readonly Estado[] {
  return TRANSICIONES[actual] ?? [];
}

export function puedeTransicionar(actual: Estado, nuevo: Estado): boolean {
  return transicionesValidas(actual).includes(nuevo);
}

export const ETIQUETA_ESTADO: Record<Estado, string> = {
  REGISTRADA: 'Registrada',
  EN_REVISION: 'En revisión',
  ATENDIDA: 'Atendida',
  SOLUCIONADA: 'Solucionada',
  CERRADA: 'Cerrada',
};

/** Estados que cuentan como "abiertos". */
export const ABIERTO = (e: Estado) => e !== 'CERRADA' && e !== 'SOLUCIONADA';
