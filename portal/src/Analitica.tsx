import { useEffect, useState } from 'react';
import { ApiError, TIPOS } from './types';
import type { Api, Dias, FilasPorReporte, Reporte, RespuestaAnalitica, Zona } from './types';
import { Aviso, fmtFecha, nombreZona } from './ui';

const ORIGEN: Record<string, string> = { USUARIO: 'Por reportes de usuarios', ANTENA: 'Detectado por antenas', RECURRENCIA: 'Recurrencia' };

export const PERIODOS: Dias[] = [7, 30, 90];

export function mensajeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 504 || e.status === 502) return 'La consulta tardó demasiado, prueba con menos días.';
    if (e.status === 403) return 'Solo los operadores pueden ver la analítica.';
    return e.message;
  }
  return e instanceof Error ? e.message : 'Error al cargar';
}

type Estado<R extends Reporte> =
  | { cargando: true }
  | { cargando: false; error: string }
  | { cargando: false; error: ''; datos: RespuestaAnalitica<R> };

function useReporte<R extends Reporte>(api: Api, reporte: R, dias: Dias): Estado<R> {
  const [s, setS] = useState<Estado<R>>({ cargando: true });
  useEffect(() => {
    let vivo = true;
    setS({ cargando: true });
    api.analitica(reporte, dias).then(
      (datos) => vivo && setS({ cargando: false, error: '', datos }),
      (e) => vivo && setS({ cargando: false, error: mensajeError(e) }),
    );
    return () => { vivo = false; };
  }, [api, reporte, dias]);
  return s;
}

function Tarjeta<R extends Reporte>({ titulo, nota, api, reporte, dias, children }: {
  titulo: string; nota: string; api: Api; reporte: R; dias: Dias;
  children: (filas: FilasPorReporte[R][]) => React.ReactNode;
}) {
  const s = useReporte(api, reporte, dias);
  return (
    <section className="an-card" aria-label={titulo} aria-busy={s.cargando}>
      <h3>{titulo}</h3>
      <p className="nota">{nota}</p>
      {s.cargando && <p className="muted" role="status">Cargando…</p>}
      {!s.cargando && s.error && <Aviso tipo="error">{s.error}</Aviso>}
      {!s.cargando && !s.error && 'datos' in s && (
        s.datos.filas.length === 0
          ? <p className="muted">Sin datos en los últimos {dias} días.</p>
          : children(s.datos.filas)
      )}
    </section>
  );
}

interface Barra { id: string; etiqueta: string; segs: number[]; total: number }
export function Barras({ filas, unidad }: { filas: Barra[]; unidad: string }) {
  const max = Math.max(1, ...filas.map((f) => f.total));
  return (
    <>
      <ul className="hbar">
        {filas.map((f) => (
          <li key={f.id}>
            <span className="et" title={f.etiqueta}>{f.etiqueta}</span>
            <span className="pista" role="img" aria-label={`${f.etiqueta}: ${f.total} ${unidad}`}>
              {f.segs.map((v, i) => v > 0 && <span key={i} className={`seg s${i + 1}`} style={{ width: `${(v / max) * 100}%` }} />)}
            </span>
            <span className="val">{f.total}</span>
          </li>
        ))}
      </ul>
      <div className="eje" aria-hidden="true"><span>0</span><span>{Math.round(max / 2)}</span><span>{max} {unidad}</span></div>
    </>
  );
}

const etiquetaTipo = (t: string) => TIPOS.find((x) => x.valor === t)?.etiqueta ?? t;
export const fmtMin = (m: number) => (m >= 120 ? `${(m / 60).toFixed(1)} h` : `${Math.round(m)} min`);
// Athena devuelve "2026-11-15 10:00:00.000" (UTC); se normaliza a ISO para fmtFecha.
const fecha = (s: string) => fmtFecha(s.includes('T') ? s : s.replace(' ', 'T') + 'Z');

export function Analitica({ api, zonas }: { api: Api; zonas: Zona[] }) {
  const [dias, setDias] = useState<Dias>(7);
  const z = (id: string) => nombreZona(zonas, id);
  const p = { api, dias };
  return (
    <section aria-label="Analítica">
      <div className="an-cab">
        <div>
          <h2>Analítica</h2>
          <p className="muted">Resumen de los últimos {dias} días. Los datos se actualizan cada pocos minutos.</p>
        </div>
        <div className="an-periodo" role="group" aria-label="Periodo">
          {PERIODOS.map((d) => (
            <button key={d} aria-pressed={dias === d} onClick={() => setDias(d)}>{d} días</button>
          ))}
        </div>
      </div>
      <div className="an-grid">
        <Tarjeta {...p} reporte="top_zonas" titulo="Zonas con más incidencias" nota="Eje: número de incidencias registradas en el periodo.">
          {(f) => (<>
            <Barras unidad="incidencias" filas={f.map((r) => ({ id: r.zonaId, etiqueta: z(r.zonaId), segs: [r.incidencias], total: r.incidencias }))} />
            <p className="nota" style={{ marginTop: '.6rem' }}>
              {f.slice(0, 3).map((r) => `${z(r.zonaId)}: ${r.abiertas} abiertas, ${r.prioridad_alta} P1/P2`).join(' · ')}
            </p>
          </>)}
        </Tarjeta>
        <Tarjeta {...p} reporte="top_antenas" titulo="Antenas con más degradaciones" nota="Eje: degradaciones (caídas + saturaciones + inhabilitaciones).">
          {(f) => (<>
            <Barras unidad="degradaciones" filas={f.map((r) => ({ id: r.antenaId, etiqueta: `${r.antenaId} · ${z(r.zonaId)}`, segs: [r.caidas, r.saturaciones, r.inhabilitaciones], total: r.degradaciones }))} />
            <ul className="an-leyenda"><li><i />Caídas</li><li><i className="s2" />Saturaciones</li><li><i className="s3" />Inhabilitaciones</li></ul>
          </>)}
        </Tarjeta>
        <Tarjeta {...p} reporte="tipos_problema" titulo="Tipos de problema" nota="Eje: incidencias por tipo de problema.">
          {(f) => <Barras unidad="incidencias" filas={f.map((r) => ({ id: r.tipo, etiqueta: etiquetaTipo(r.tipo), segs: [r.incidencias], total: r.incidencias }))} />}
        </Tarjeta>
        <Tarjeta {...p} reporte="mttr" titulo="Tiempo medio de resolución (MTTR)" nota="Incidencias solucionadas en el periodo, por zona y prioridad.">
          {(f) => (
            <div className="scroll-x">
              <table>
                <caption className="visually-hidden">MTTR por zona y prioridad</caption>
                <thead><tr><th scope="col">Zona</th><th scope="col">Prior.</th><th scope="col" className="num">Resueltas</th><th scope="col" className="num">MTTR</th><th scope="col" className="num">Mediana</th><th scope="col" className="num">Máximo</th></tr></thead>
                <tbody>
                  {f.map((r) => (
                    <tr key={r.zonaId + r.nivelPrioridad}>
                      <td>{z(r.zonaId)}</td><td>{r.nivelPrioridad}</td>
                      <td className="num">{r.resueltas}</td><td className="num">{fmtMin(r.mttr_min)}</td>
                      <td className="num">{fmtMin(r.mediana_min)}</td><td className="num">{fmtMin(r.maximo_min)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Tarjeta>
        <Tarjeta {...p} reporte="colectivos" titulo="Incidentes colectivos recientes" nota="Más recientes primero.">
          {(f) => (
            <ul className="an-lista">
              {f.map((r) => (
                <li key={r.incidenteColectivoId}>
                  <strong>{z(r.zonaId)}</strong> · {ORIGEN[r.origen ?? ''] ?? 'Colectivo'}{r.nivelPrioridad ? ` · ${r.nivelPrioridad}` : ''} · {r.totalIncidencias} reportes agrupados
                  <br /><span className="muted">Detectado {fecha(r.detectado_en)} · {r.minutos_hasta_resolver != null ? `resuelto en ${r.minutos_hasta_resolver} min` : 'en curso'}</span>
                </li>
              ))}
            </ul>
          )}
        </Tarjeta>
        <Tarjeta {...p} reporte="recurrencia" titulo="Zonas con recurrencia" nota="Zonas con más de un incidente colectivo en el periodo.">
          {(f) => (
            <ul className="an-lista">
              {f.map((r) => (
                <li key={r.zonaId}>
                  <strong>{z(r.zonaId)}</strong> · {r.colectivos} colectivos
                  <br /><span className="muted">Del {fecha(r.primer_colectivo)} al {fecha(r.ultimo_colectivo)}</span>
                </li>
              ))}
            </ul>
          )}
        </Tarjeta>
      </div>
    </section>
  );
}
