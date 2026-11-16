import { ApiError } from './types';
import type { Api, ApiPublica, Estado, FiltrosIncidencias, Incidencia, Pagina, ReporteAceptado, ReportePublico, RespuestaConsulta, Transicion, Zona, Antena, EstadoAntena, Reporte, Operador } from './types';

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

/** Cliente de la API pública (sin token): reportar una falla y consultar reportes. */
export function crearApiPublica(apiUrl: string, fetchImpl: Fetch = (...a) => fetch(...a)): ApiPublica {
  const pedir = crearPedir(apiUrl, () => null, fetchImpl);
  return {
    reportarFalla: (r: ReportePublico) => pedir<ReporteAceptado>('POST', '/reportes', r),
    consultarReportes: (documento: string, numeroServicio: string) =>
      pedir<RespuestaConsulta>('POST', '/reportes/consulta', { documento, numeroServicio }).then((r) => ({
        ...r,
        reportes: r?.reportes ?? [],
      })),
  };
}

function crearPedir(apiUrl: string, obtenerToken: () => string | null, fetchImpl: Fetch) {
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
        (datos && typeof datos === 'object' ? datos : {}) as Record<string, unknown>,
      );
    }
    return datos as T;
  }
  return pedir;
}

export function crearApi(
  apiUrl: string,
  obtenerToken: () => string | null,
  fetchImpl: Fetch = (...a) => fetch(...a),
): Api {
  const pedir = crearPedir(apiUrl, obtenerToken, fetchImpl);
  const id = encodeURIComponent;
  return {
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
    listarOperadores: () => pedir<Operador[]>('GET', '/operadores').then((r) => r ?? []),
    promoverOperador: (email: string) => pedir<Operador>('POST', '/operadores', { email }),
    quitarOperador: (email: string) => pedir<Operador>('DELETE', `/operadores/${id(email)}`),
    analitica: (reporte: Reporte, dias = 7) =>
      pedir<any>('GET', `/analitica/${id(reporte)}` + consulta({ dias })).then((r) => ({
        ...r,
        filas: r?.filas ?? [],
        total: r?.total ?? r?.filas?.length ?? 0,
      })),
  };
}
