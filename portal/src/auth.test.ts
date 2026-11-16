import { describe, expect, it } from 'vitest';
import { rolDesdeGrupos, sesionDesdeIdToken, sesionVigente } from './auth';
import { crearDemo } from './demo';
import { ApiError } from './types';

function jwt(payload: object) {
  const b = (o: object) => Buffer.from(JSON.stringify(o)).toString('base64url');
  return `${b({ alg: 'none' })}.${b(payload)}.firma`;
}

describe('sesión y roles', () => {
  it('operadores => rol operador', () => {
    const s = sesionDesdeIdToken(jwt({ sub: 's', email: 'o@t.pe', exp: 4102444800, 'cognito:groups': ['operadores'] }));
    expect(s.rol).toBe('operador');
    expect(s.email).toBe('o@t.pe');
  });
  it('sin grupo => usuario', () => {
    expect(sesionDesdeIdToken(jwt({ exp: 1 })).rol).toBe('usuario');
    expect(rolDesdeGrupos(['otros'])).toBe('usuario');
  });
  it('detecta expiración', () => {
    const s = sesionDesdeIdToken(jwt({ exp: 1000 }));
    expect(sesionVigente(s, 500_000)).toBe(true);
    expect(sesionVigente(s, 1_000_000)).toBe(false);
    expect(sesionVigente(null)).toBe(false);
  });
});

describe('modo demo', () => {
  it('tiene un colectivo con 12 reportes y rechaza transiciones inválidas con 409', async () => {
    const api = crearDemo('operador');
    const col = (await api.listarIncidencias({ clase: 'COLECTIVA', limite: 200 })).items.find((i) => i.usuariosAfectados === 12)!;
    const hijos = (await api.listarIncidencias({ limite: 200 })).items.filter((i) => i.incidenteColectivoId === col.incidenciaId);
    expect(hijos).toHaveLength(12);
    const e = await api.cambiarEstado(col.incidenciaId, 'ATENDIDA').then(() => null, (x) => x);
    expect(e).toBeNull(); // EN_REVISION -> ATENDIDA es válida
    const e2 = await api.cambiarEstado(col.incidenciaId, 'CERRADA').catch((x) => x);
    expect(e2).toBeInstanceOf(ApiError); // ATENDIDA -> CERRADA no
    expect(e2.status).toBe(409);
  });
  it('el usuario solo ve lo suyo y no cambia estados', async () => {
    const api = crearDemo('usuario');
    const mis = await api.listarIncidencias();
    expect(mis.items.length).toBeGreaterThan(0);
    expect(mis.items.length).toBeLessThan(10);
    const e = await api.cambiarEstado(mis.items[0].incidenciaId, 'EN_REVISION').catch((x) => x);
    expect(e.status).toBe(403);
  });
});
