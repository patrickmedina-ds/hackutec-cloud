import { describe, expect, it, vi } from 'vitest';
import { crearApi } from './api';
import { analiticaDemo, crearDemo, ZONAS_DEMO } from './demo';
import { mensajeError } from './Analitica';
import { ApiError } from './types';

const resp = (status: number, cuerpo?: unknown) => new Response(cuerpo === undefined ? null : JSON.stringify(cuerpo), { status });

describe('cliente de analítica', () => {
  it('pide /analitica/{reporte}?dias=N con token', async () => {
    const f = vi.fn(async () => resp(200, { reporte: 'top_zonas', dias: 30, total: 1, filas: [{ zonaId: 'LIM-SJL', incidencias: 3, abiertas: 1, prioridad_alta: 0 }], cache: false }));
    const api = crearApi('https://api.test/dev/', () => 'TOK', f as unknown as typeof fetch);
    const r = await api.analitica('top_zonas', 30);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://api.test/dev/analitica/top_zonas?dias=30');
    expect((init.headers as Record<string, string>).Authorization).toBe('TOK');
    expect(r.filas[0].incidencias).toBe(3);
  });

  it('usa 7 días por defecto y tolera filas ausentes', async () => {
    const f = vi.fn(async () => resp(200, { reporte: 'mttr', dias: 7 }));
    const api = crearApi('https://api.test', () => null, f as unknown as typeof fetch);
    const r = await api.analitica('mttr');
    expect((f.mock.calls[0] as unknown as [string])[0]).toBe('https://api.test/analitica/mttr?dias=7');
    expect(r.filas).toEqual([]);
    expect(r.total).toBe(0);
  });

  it('errores 502 y 504 dan mensaje claro; 403 también', async () => {
    for (const s of [502, 504]) {
      const api = crearApi('https://a', () => 't', (async () => resp(s, { mensaje: 'x' })) as unknown as typeof fetch);
      const e = await api.analitica('mttr', 90).catch((x) => x);
      expect(e).toBeInstanceOf(ApiError);
      expect(e.status).toBe(s);
      expect(mensajeError(e)).toBe('La consulta tardó demasiado, prueba con menos días.');
    }
    expect(mensajeError(new ApiError(403, 'X', 'solo operadores'))).toContain('operadores');
  });
});

describe('analítica en modo demo', () => {
  const reportes = ['top_zonas', 'top_antenas', 'tipos_problema', 'mttr', 'colectivos', 'recurrencia'] as const;
  it('devuelve filas coherentes con zonas y antenas demo', async () => {
    const demo = crearDemo('operador');
    const antenas = await demo.listarAntenas();
    for (const r of reportes) {
      const d = await demo.analitica(r, 30);
      expect(d.reporte).toBe(r);
      expect(d.total).toBe(d.filas.length);
      expect(d.filas.length).toBeGreaterThan(0);
      for (const f of d.filas as { zonaId?: string; antenaId?: string }[]) {
        if (f.zonaId) expect(ZONAS_DEMO.map((z) => z.zonaId)).toContain(f.zonaId);
        if (f.antenaId) expect(antenas.map((a) => a.antenaId)).toContain(f.antenaId);
      }
    }
  });
  it('escala con el periodo y mantiene el orden descendente', () => {
    const a = analiticaDemo('top_zonas', 7).filas;
    const b = analiticaDemo('top_zonas', 90).filas;
    expect(b[0].incidencias).toBeGreaterThan(a[0].incidencias);
    expect(a.map((x) => x.incidencias)).toEqual([...a.map((x) => x.incidencias)].sort((x, y) => y - x));
    const t = analiticaDemo('top_antenas').filas;
    expect(t.every((x) => x.degradaciones === x.caidas + x.saturaciones + x.inhabilitaciones)).toBe(true);
  });
  it('el rol usuario no accede', async () => {
    const demo = crearDemo('usuario');
    await expect(demo.analitica('top_zonas')).rejects.toBeInstanceOf(ApiError);
  });
});
