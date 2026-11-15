import { ApiError } from './types';
import type { Api, Estado, FiltrosIncidencias, Incidencia, NuevoReporte, Pagina, Transicion, Zona, Antena, EstadoAntena, Reporte } from './types';

type Fetch = typeof fetch;

export function consulta(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}

export function crearApi(
  apiUrl: string,
  obtenerToken: () => string | null,
  fetchImpl: Fetch = (...a) => fetch(...a),
): Api {
  const base = apiUrl.replace(/\/+$/, '');

  async function pedir<T>(metodo: string, ruta: string, cuerpo?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    const token = obtenerToken();
    if (token) headers.Authorization = token;
    if (cuerpo !== undefined) headers['Content-Type'] = 'application/json';
    let resp: Response;
    try {
      resp = await fetchImpl(base + ruta, {
        method: metodo,
        headers,
        body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
      });
    } catch {
      throw new ApiError(0, 'RED', 'No se pudo contactar con el servidor. Revisa tu conexión.');
    }
    const texto = await resp.text();
    let datos: unknown = null;
    if (texto) {
      try {
        datos = JSON.parse(texto);
      } catch {
        datos = null;
      }
    }
    if (!resp.ok) {
      const e = (datos ?? {}) as { codigo?: string; mensaje?: string; message?: string };
      throw new ApiError(
        resp.status,
        e.codigo ?? `HTTP_${resp.status}`,
        e.mensaje ?? e.message ?? `Error ${resp.status}`,
      );
    }
    return datos as T;
  }

  const id = encodeURIComponent;
  return {
    crearReporte: (r: NuevoReporte) => pedir('POST', '/incidencias', r),
    listarIncidencias: (f: FiltrosIncidencias = {}) =>
      pedir<Pagina>(
        'GET',
        '/incidencias' +
          consulta({
            zonaId: f.zonaId,
            estado: f.estado,
            clase: f.clase,
            desde: f.desde,
            limite: f.limite,
            cursor: f.cursor,
          }),
      ).then((p) => ({ items: p.items ?? [], cursor: p.cursor ?? null })),
    obtenerIncidencia: (i: string) => pedir<Incidencia>('GET', `/incidencias/${id(i)}`),
    historial: (i: string) => pedir<Transicion[]>('GET', `/incidencias/${id(i)}/historial`),
    cambiarEstado: (i: string, estadoNuevo: Estado, comentario?: string) =>
      pedir<Incidencia>('PATCH', `/incidencias/${id(i)}/estado`, {
        estadoNuevo,
        ...(comentario ? { comentario } : {}),
      }),
    listarZonas: () => pedir<Zona[]>('GET', '/zonas'),
    listarAntenas: (f: { zonaId?: string; estado?: EstadoAntena | '' } = {}) =>
      pedir<Antena[]>('GET', '/antenas' + consulta({ zonaId: f.zonaId, estado: f.estado })),
    analitica: (reporte: Reporte, dias = 7) =>
      pedir<any>('GET', `/analitica/${id(reporte)}` + consulta({ dias })).then((r) => ({
        ...r,
        filas: r?.filas ?? [],
        total: r?.total ?? r?.filas?.length ?? 0,
      })),
  };
}
