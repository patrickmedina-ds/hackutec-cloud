import { describe, expect, it, vi } from 'vitest';
import { consulta, crearApi } from './api';
import { ApiError } from './types';

function respuesta(status: number, cuerpo?: unknown): Response {
  return new Response(cuerpo === undefined ? null : JSON.stringify(cuerpo), { status });
}

function setup(resp: Response | (() => Promise<Response>)) {
  const f = vi.fn(async () => {
    if (typeof resp === 'function') return resp();
    return resp.clone();
  });
  const api = crearApi('https://api.test/dev/', () => 'ID.TOKEN', f as unknown as typeof fetch);
  return { f, api };
}

describe('cliente de la API', () => {
  it('consulta ignora vacíos', () => {
    expect(consulta({ a: 'x', b: '', c: undefined, d: 5 })).toBe('?a=x&d=5');
    expect(consulta({})).toBe('');
  });

  it('envía el ID token en Authorization y arma la query del listado', async () => {
    const { f, api } = setup(respuesta(200, { items: [{ incidenciaId: 'a' }], cursor: 'CUR' }));
    const p = await api.listarIncidencias({ zonaId: 'LIM-SJL', estado: 'EN_REVISION', clase: '', limite: 25, cursor: 'PREV' });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://api.test/dev/incidencias?zonaId=LIM-SJL&estado=EN_REVISION&limite=25&cursor=PREV');
    expect((init.headers as Record<string, string>).Authorization).toBe('ID.TOKEN');
    expect(init.method).toBe('GET');
    expect(p.cursor).toBe('CUR');
    expect(p.items).toHaveLength(1);
  });

  it('normaliza cursor ausente a null', async () => {
    const { api } = setup(respuesta(200, { items: [] }));
    expect((await api.listarIncidencias()).cursor).toBeNull();
  });

  it('POST de reporte manda JSON y devuelve incidenciaId', async () => {
    const { f, api } = setup(respuesta(202, { incidenciaId: 'req-1', estado: 'REGISTRADA' }));
    const r = await api.crearReporte({
      documento: '12345678', numeroServicio: '987654', zonaId: 'LIM-MIR', fechaHoraEvento: '2026-11-15T14:03:00Z', tipo: 'SIN_INTERNET', descripcion: 'x',
    });
    expect(r.incidenciaId).toBe('req-1');
    const [, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
    expect(JSON.parse(init.body as string).zonaId).toBe('LIM-MIR');
  });

  it('PATCH de estado escapa el id e incluye comentario solo si existe', async () => {
    const { f, api } = setup(respuesta(200, { incidenciaId: 'a/b' }));
    await api.cambiarEstado('a/b', 'ATENDIDA', 'en camino');
    await api.cambiarEstado('a/b', 'ATENDIDA');
    const [u1, i1] = f.mock.calls[0] as unknown as [string, RequestInit];
    const [, i2] = f.mock.calls[1] as unknown as [string, RequestInit];
    expect(u1).toBe('https://api.test/dev/incidencias/a%2Fb/estado');
    expect(i1.method).toBe('PATCH');
    expect(JSON.parse(i1.body as string)).toEqual({ estadoNuevo: 'ATENDIDA', comentario: 'en camino' });
    expect(JSON.parse(i2.body as string)).toEqual({ estadoNuevo: 'ATENDIDA' });
  });

  it('convierte un 409 en ApiError con código y mensaje', async () => {
    const { api } = setup(respuesta(409, { codigo: 'TRANSICION_INVALIDA', mensaje: 'REGISTRADA → ATENDIDA no está permitido' }));
    const e = await api.cambiarEstado('a', 'ATENDIDA').catch((x) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e.status).toBe(409);
    expect(e.codigo).toBe('TRANSICION_INVALIDA');
    expect(e.message).toContain('no está permitido');
  });

  it('errores sin cuerpo JSON dan mensaje genérico', async () => {
    const { api } = setup(respuesta(500));
    const e = await api.listarZonas().catch((x) => x);
    expect(e.status).toBe(500);
    expect(e.message).toBe('Error 500');
  });

  it('falla de red se reporta como ApiError RED', async () => {
    const { api } = setup(() => Promise.reject(new TypeError('fail')));
    const e = await api.listarAntenas({ estado: 'CAIDA' }).catch((x) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e.codigo).toBe('RED');
  });

  it('historial y zonas usan las rutas del contrato', async () => {
    const { f, api } = setup(respuesta(200, []));
    await api.historial('x1');
    await api.listarZonas();
    await api.listarAntenas({ zonaId: 'LIM-SJL' });
    expect(f.mock.calls.map((c) => (c as unknown as [string])[0])).toEqual([
      'https://api.test/dev/incidencias/x1/historial',
      'https://api.test/dev/zonas',
      'https://api.test/dev/antenas?zonaId=LIM-SJL',
    ]);
  });
});
