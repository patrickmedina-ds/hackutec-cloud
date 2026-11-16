import { describe, expect, it, vi } from 'vitest';
import { crearApiPublica } from './api';
import { aReportePublico, crearDemoPublica, mensajeError, serviciosDeError, validarFalla } from './publico';
import { ApiError } from './types';

function setup(status: number, cuerpo?: unknown) {
  const f = vi.fn(async () => new Response(cuerpo === undefined ? null : JSON.stringify(cuerpo), { status }));
  return { f, api: crearApiPublica('https://api.test/dev/', f as unknown as typeof fetch) };
}

describe('cliente de la API pública', () => {
  it('POST /reportes sin Authorization', async () => {
    const { f, api } = setup(202, { incidenciaId: 'u-1', estado: 'REGISTRADA', zonaId: 'LIM-JMA-01', zonaNombre: 'Jesús María' });
    const r = await api.reportarFalla({ documento: '70000001', tipo: 'SIN_INTERNET', descripcion: 'x' });
    expect(r.zonaNombre).toBe('Jesús María');
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://api.test/dev/reportes');
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
    expect(JSON.parse(init.body as string)).toEqual({ documento: '70000001', tipo: 'SIN_INTERNET', descripcion: 'x' });
  });

  it('409 conserva la lista de servicios', async () => {
    const servicios = [{ numeroServicio: 'SRV-****0002', zonaId: 'A', zonaNombre: 'A' }];
    const { api } = setup(409, { codigo: 'VARIOS_SERVICIOS', mensaje: 'm', servicios });
    const e = await api.reportarFalla({ documento: '70000002', tipo: 'OTRO', descripcion: 'x' }).catch((x) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(serviciosDeError(e)).toEqual(servicios);
    expect(mensajeError(e)).toContain('más de un servicio');
  });

  it('404 y 403 dan mensajes claros', () => {
    expect(mensajeError(new ApiError(404, 'CLIENTE_NO_ENCONTRADO', 'x'))).toContain('No encontramos un servicio');
    expect(mensajeError(new ApiError(403, 'CONTRATO_SUSPENDIDO', 'x'))).toContain('suspendido');
  });

  it('consulta usa POST /reportes/consulta', async () => {
    const { f, api } = setup(200, { servicio: 'SRV-****0001', zonaId: 'Z', zonaNombre: 'Z' });
    const r = await api.consultarReportes('70000001', 'SRV-00000001');
    expect(r.reportes).toEqual([]);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://api.test/dev/reportes/consulta');
    expect(JSON.parse(init.body as string)).toEqual({ documento: '70000001', numeroServicio: 'SRV-00000001' });
  });
});

describe('formulario público', () => {
  const ok = { identificador: '70000001', tipo: 'LENTITUD' as const, descripcion: 'lento', fechaHoraLocal: '2026-11-15T10:00' };
  const ahora = new Date('2026-11-15T20:00:00');
  it('acepta DNI o número de servicio', () => {
    expect(validarFalla(ok, ahora)).toEqual({});
    expect(validarFalla({ ...ok, identificador: 'srv-00000001' }, ahora)).toEqual({});
    expect(aReportePublico(ok).documento).toBe('70000001');
    expect(aReportePublico({ ...ok, identificador: ' srv-00000001 ' }).numeroServicio).toBe('SRV-00000001');
    const elegido = aReportePublico(ok, 'SRV-****0002');
    expect(elegido).toMatchObject({ documento: '70000001', numeroServicio: 'SRV-****0002' });
    expect(elegido.fechaHoraEvento).toMatch(/Z$/);
  });
  it('marca errores', () => {
    const e = validarFalla({ identificador: '123', tipo: '', descripcion: ' ', fechaHoraLocal: '2026-12-01T10:00' }, ahora);
    expect(Object.keys(e).sort()).toEqual(['descripcion', 'fechaHoraLocal', 'identificador', 'tipo']);
  });
});

describe('modo demo público', () => {
  const base = { tipo: 'SIN_INTERNET' as const, descripcion: 'sin datos' };
  it('reporta con DNI y aparece en la consulta', async () => {
    const d = crearDemoPublica(undefined, 0);
    const r = await d.reportarFalla({ ...base, documento: '70000001' });
    expect(r.zonaId).toBe('LIM-JMA-01');
    const c = await d.consultarReportes('70000001', 'SRV-00000001');
    expect(c.reportes[0].incidenciaId).toBe(r.incidenciaId);
    expect(c.servicio).toBe('SRV-****0001');
    expect(JSON.stringify(c)).not.toContain('70000001');
  });
  it('404, 409 con elección y 403', async () => {
    const d = crearDemoPublica(undefined, 0);
    await expect(d.reportarFalla({ ...base, documento: '11111111' })).rejects.toMatchObject({ status: 404 });
    const e = await d.reportarFalla({ ...base, documento: '70000002' }).catch((x) => x);
    expect(e.status).toBe(409);
    const [, segundo] = serviciosDeError(e);
    const ok = await d.reportarFalla({ ...base, documento: '70000002', numeroServicio: segundo.numeroServicio });
    expect(ok.zonaId).toBe('LIM-MIR-01');
    await expect(d.reportarFalla({ ...base, numeroServicio: 'SRV-00000004' })).rejects.toMatchObject({ status: 403 });
    await expect(d.consultarReportes('70000002', 'SRV-00000001')).rejects.toMatchObject({ status: 404 });
  });
});
