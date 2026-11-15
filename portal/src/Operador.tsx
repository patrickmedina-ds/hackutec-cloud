import { useCallback, useEffect, useMemo, useState } from 'react';
import { ABIERTO, ETIQUETA_ESTADO, transicionesValidas } from './transiciones';
import { ApiError, ESTADOS, TIPOS } from './types';
import type { Antena, Api, Clase, Estado, Incidencia, Transicion, Zona } from './types';
import { Analitica } from './Analitica';
import { AntenaBadge, Aviso, EstadoBadge, LineaTiempo, PrioridadChip, SeveridadChip, fmtFecha, nombreZona } from './ui';

const etiquetaTipo = (t: string) => TIPOS.find((x) => x.valor === t)?.etiqueta ?? t;

/* ---------- Resumen ---------- */
function Resumen({ api, version }: { api: Api; version: number }) {
  const [d, setD] = useState<{ col: number; p1: number; ant: number; muestra: number } | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    Promise.all([api.listarIncidencias({ limite: 200 }), api.listarAntenas()])
      .then(([p, a]) =>
        setD({
          col: p.items.filter((i) => i.clase === 'COLECTIVA' && ABIERTO(i.estado)).length,
          p1: p.items.filter((i) => i.nivelPrioridad === 'P1' && ABIERTO(i.estado)).length,
          ant: a.filter((x) => x.estado !== 'NORMAL').length,
          muestra: p.items.length,
        }))
      .catch((e) => setErr(e.message));
  }, [api, version]);
  if (err) return <Aviso tipo="error">No se pudo calcular el resumen: {err}</Aviso>;
  const celdas: [string, number | undefined, string][] = [
    ['Colectivos activos', d?.col, 'col'],
    ['P1 abiertos', d?.p1, 'p1'],
    ['Antenas degradadas', d?.ant, 'ant'],
  ];
  return (
    <section aria-label="Resumen" className="resumen">
      {celdas.map(([t, v, c]) => (
        <div key={t} className={`kpi kpi-${c} ${v ? 'kpi-alerta' : ''}`}>
          <span className="kpi-num">{v ?? '…'}</span>
          <span className="kpi-et">{t}</span>
        </div>
      ))}
      {d && <p className="muted resumen-nota">Calculado sobre las {d.muestra} incidencias más recientes.</p>}
    </section>
  );
}

/* ---------- Detalle ---------- */
function Detalle({ api, id, zonas, onCambio, onCerrar, onAbrir }: {
  api: Api; id: string; zonas: Zona[]; onCambio: () => void; onCerrar: () => void; onAbrir: (id: string) => void;
}) {
  const [inc, setInc] = useState<Incidencia | null>(null);
  const [hist, setHist] = useState<Transicion[]>([]);
  const [hijos, setHijos] = useState<Incidencia[]>([]);
  const [err, setErr] = useState('');
  const [errEstado, setErrEstado] = useState('');
  const [comentario, setComentario] = useState('');
  const [cambiando, setCambiando] = useState(false);

  const cargar = useCallback(async () => {
    setErr('');
    try {
      const i = await api.obtenerIncidencia(id);
      const [h, ch] = await Promise.all([
        api.historial(id),
        i.clase === 'COLECTIVA' ? api.listarIncidencias({ limite: 200 }).then((p) => p.items.filter((x) => x.incidenteColectivoId === id)) : Promise.resolve([]),
      ]);
      setInc(i); setHist(h); setHijos(ch);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Error al cargar');
    }
  }, [api, id]);
  useEffect(() => { setInc(null); setErrEstado(''); setComentario(''); void cargar(); }, [cargar]);

  async function mover(nuevo: Estado) {
    setCambiando(true);
    setErrEstado('');
    try {
      await api.cambiarEstado(id, nuevo, comentario.trim() || undefined);
      setComentario('');
      await cargar();
      onCambio();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setErrEstado(`Conflicto (409): ${e.message}. La incidencia pudo cambiar mientras la veías; se recargó el estado actual.`);
        await cargar();
      } else {
        setErrEstado(e instanceof Error ? e.message : 'No se pudo cambiar el estado');
      }
    } finally {
      setCambiando(false);
    }
  }

  return (
    <aside className="detalle" aria-label="Detalle de incidencia">
      <div className="fila entre">
        <h3>Detalle</h3>
        <button className="btn btn-sec" onClick={onCerrar}>Cerrar</button>
      </div>
      {err && <Aviso tipo="error">{err}</Aviso>}
      {!inc && !err && <p className="muted">Cargando…</p>}
      {inc && (
        <>
          <p className="mono muted">{inc.incidenciaId}</p>
          <div className="fila chips">
            <EstadoBadge estado={inc.estado} />
            <PrioridadChip nivel={inc.nivelPrioridad} />
            <SeveridadChip sev={inc.severidadIA} />
            <span className="chip neutro">{inc.clase === 'COLECTIVA' ? 'Colectiva' : 'Individual'}</span>
          </div>
          <dl className="datos">
            <dt>Zona</dt><dd>{nombreZona(zonas, inc.zonaId)}</dd>
            <dt>Tipo</dt><dd>{etiquetaTipo(inc.tipo)}</dd>
            <dt>Origen</dt><dd>{inc.origen.toLowerCase()}</dd>
            <dt>Ocurrió</dt><dd>{fmtFecha(inc.fechaHoraEvento)}</dd>
            <dt>Creada</dt><dd>{fmtFecha(inc.creadoEn)}</dd>
            {inc.usuariosAfectados != null && (<><dt>Usuarios afectados</dt><dd>{inc.usuariosAfectados}</dd></>)}
            {inc.antenasAfectadas?.length ? (<><dt>Antenas</dt><dd>{inc.antenasAfectadas.join(', ')}</dd></>) : null}
            {inc.prioridad != null && (<><dt>Puntaje</dt><dd>{inc.prioridad}/100</dd></>)}
          </dl>
          <p>{inc.descripcion}</p>

          <div className="ia">
            <strong>Resumen de la IA</strong>
            <p>{inc.resumenIA ?? 'La IA aún no analizó esta incidencia.'}</p>
          </div>

          <h4>Cambiar estado</h4>
          {transicionesValidas(inc.estado).length === 0 ? (
            <p className="muted">Estado final: no hay transiciones disponibles.</p>
          ) : (
            <>
              <label htmlFor="coment" className="etiqueta">Comentario (opcional)</label>
              <textarea id="coment" rows={2} maxLength={500} value={comentario} onChange={(e) => setComentario(e.target.value)} />
              <div className="fila chips">
                {transicionesValidas(inc.estado).map((e) => (
                  <button key={e} className="btn btn-sec" disabled={cambiando} onClick={() => mover(e)}>
                    Pasar a {ETIQUETA_ESTADO[e].toLowerCase()}
                  </button>
                ))}
              </div>
            </>
          )}
          {errEstado && <Aviso tipo="error">{errEstado}</Aviso>}

          <h4>Historial</h4>
          <LineaTiempo filas={hist} />
          {hist.length > 0 && (
            <details>
              <summary>Ver registro completo ({hist.length})</summary>
              <ul className="registro">
                {hist.map((h, i) => (
                  <li key={i}><span className="mono">{fmtFecha(h.ts)}</span> {h.estadoAnterior ? `${h.estadoAnterior} → ` : ''}{h.estadoNuevo} · {h.actor}{h.comentario ? ` · “${h.comentario}”` : ''}</li>
                ))}
              </ul>
            </details>
          )}

          {inc.clase === 'COLECTIVA' && (
            <>
              <h4>Reportes agrupados ({hijos.length})</h4>
              {hijos.length === 0 ? <p className="muted">Sin reportes hijos asociados.</p> : (
                <ul className="registro hijos">
                  {hijos.map((h) => (
                    <li key={h.incidenciaId}>
                      <button className="btn-link" onClick={() => onAbrir(h.incidenciaId)}>{h.incidenciaId}</button>{' '}
                      <EstadoBadge estado={h.estado} /> <span className="muted">{etiquetaTipo(h.tipo)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
          {inc.incidenteColectivoId && (
            <p>Pertenece al colectivo <button className="btn-link" onClick={() => onAbrir(inc.incidenteColectivoId!)}>{inc.incidenteColectivoId}</button></p>
          )}
        </>
      )}
    </aside>
  );
}

/* ---------- Tabla ---------- */
function Tabla({ api, zonas, version, onCambio }: { api: Api; zonas: Zona[]; version: number; onCambio: () => void }) {
  const [filtros, setFiltros] = useState<{ zonaId: string; estado: Estado | ''; clase: Clase | ''; desde: string }>({ zonaId: '', estado: '', clase: '', desde: '' });
  const [items, setItems] = useState<Incidencia[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);
  const [err, setErr] = useState('');
  const [sel, setSel] = useState<string | null>(null);

  const pedir = useCallback(async (cur: string | null) => {
    setCargando(true); setErr('');
    try {
      const p = await api.listarIncidencias({
        zonaId: filtros.zonaId || undefined, estado: filtros.estado, clase: filtros.clase,
        desde: filtros.desde ? new Date(filtros.desde).toISOString() : undefined, limite: 25, cursor: cur,
      });
      setItems((prev) => (cur ? [...prev, ...p.items] : p.items));
      setCursor(p.cursor);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Error al cargar');
    } finally {
      setCargando(false);
    }
  }, [api, filtros]);
  useEffect(() => { void pedir(null); }, [pedir, version]);

  const f = <K extends keyof typeof filtros>(k: K, v: (typeof filtros)[K]) => setFiltros((p) => ({ ...p, [k]: v }));

  return (
    <section className={`tabla-wrap ${sel ? 'con-detalle' : ''}`}>
      <div>
        <h2>Incidencias</h2>
        <form className="filtros" onSubmit={(e) => e.preventDefault()} aria-label="Filtros">
          <label>Zona
            <select value={filtros.zonaId} onChange={(e) => f('zonaId', e.target.value)}>
              <option value="">Todas</option>
              {zonas.map((z) => <option key={z.zonaId} value={z.zonaId}>{z.nombre}</option>)}
            </select>
          </label>
          <label>Estado
            <select value={filtros.estado} onChange={(e) => f('estado', e.target.value as Estado | '')}>
              <option value="">Todos</option>
              {ESTADOS.map((e) => <option key={e} value={e}>{ETIQUETA_ESTADO[e]}</option>)}
            </select>
          </label>
          <label>Clase
            <select value={filtros.clase} onChange={(e) => f('clase', e.target.value as Clase | '')}>
              <option value="">Todas</option>
              <option value="INDIVIDUAL">Individual</option>
              <option value="COLECTIVA">Colectiva</option>
            </select>
          </label>
          <label>Desde
            <input type="datetime-local" value={filtros.desde} onChange={(e) => f('desde', e.target.value)} />
          </label>
        </form>
        {err && <Aviso tipo="error">{err}</Aviso>}
        <div className="scroll-x">
          <table>
            <caption className="visually-hidden">Incidencias, más recientes primero</caption>
            <thead>
              <tr><th scope="col">Prior.</th><th scope="col">Estado</th><th scope="col">Zona</th><th scope="col">Tipo</th><th scope="col">Clase</th><th scope="col">Severidad IA</th><th scope="col">Creada</th></tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.incidenciaId} className={sel === i.incidenciaId ? 'sel' : ''}>
                  <td><PrioridadChip nivel={i.nivelPrioridad} /></td>
                  <td><EstadoBadge estado={i.estado} /></td>
                  <td>{nombreZona(zonas, i.zonaId)}</td>
                  <td>{etiquetaTipo(i.tipo)}</td>
                  <td>{i.clase === 'COLECTIVA' ? `Colectiva${i.usuariosAfectados ? ` · ${i.usuariosAfectados}` : ''}` : 'Individual'}</td>
                  <td><SeveridadChip sev={i.severidadIA} /></td>
                  <td>
                    <button className="btn-link" onClick={() => setSel(i.incidenciaId)} aria-label={`Ver detalle de ${i.incidenciaId}`}>{fmtFecha(i.creadoEn)}</button>
                  </td>
                </tr>
              ))}
              {!cargando && items.length === 0 && <tr><td colSpan={7} className="muted">Sin resultados con estos filtros.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="fila pie-tabla">
          {cargando && <span className="muted">Cargando…</span>}
          {cursor && !cargando && <button className="btn btn-sec" onClick={() => pedir(cursor)}>Cargar más</button>}
          {!cursor && !cargando && items.length > 0 && <span className="muted">Fin de la lista · {items.length} incidencias</span>}
        </div>
      </div>
      {sel && <Detalle api={api} id={sel} zonas={zonas} onCambio={() => { onCambio(); }} onCerrar={() => setSel(null)} onAbrir={setSel} />}
    </section>
  );
}

/* ---------- Antenas ---------- */
function Antenas({ api, zonas, version }: { api: Api; zonas: Zona[]; version: number }) {
  const [antenas, setAntenas] = useState<Antena[]>([]);
  const [err, setErr] = useState('');
  useEffect(() => { api.listarAntenas().then(setAntenas).catch((e) => setErr(e.message)); }, [api, version]);
  const porZona = useMemo(() => {
    const m = new Map<string, Antena[]>();
    for (const a of antenas) m.set(a.zonaId, [...(m.get(a.zonaId) ?? []), a]);
    return [...m.entries()].sort((a, b) => nombreZona(zonas, a[0]).localeCompare(nombreZona(zonas, b[0])));
  }, [antenas, zonas]);
  return (
    <section className="antenas">
      <h2>Antenas por zona</h2>
      {err && <Aviso tipo="error">{err}</Aviso>}
      <div className="zonas-grid">
        {porZona.map(([zonaId, as]) => {
          const mal = as.filter((a) => a.estado !== 'NORMAL').length;
          return (
            <article key={zonaId} className={`zona-card ${mal ? 'zona-alerta' : ''}`}>
              <h3>{nombreZona(zonas, zonaId)} {mal > 0 && <span className="muted">· {mal} con problemas</span>}</h3>
              <ul>
                {as.map((a) => (
                  <li key={a.antenaId}>
                    <span className="mono">{a.antenaId}</span>
                    <AntenaBadge estado={a.estado} />
                    <span className="gb">{a.gbPorMinuto.toFixed(1)} GB/min</span>
                  </li>
                ))}
              </ul>
            </article>
          );
        })}
      </div>
    </section>
  );
}

export function OperadorVista({ api }: { api: Api }) {
  const [zonas, setZonas] = useState<Zona[]>([]);
  const [version, setVersion] = useState(0);
  const [err, setErr] = useState('');
  const [sub, setSub] = useState<'op' | 'an'>('op');
  useEffect(() => { api.listarZonas().then(setZonas).catch((e) => setErr(e.message)); }, [api]);
  return (
    <>
      {err && <Aviso tipo="error">No se cargaron las zonas: {err}</Aviso>}
      <nav className="tabs" aria-label="Secciones del panel">
        <button className="tab" aria-current={sub === 'op' ? 'page' : undefined} onClick={() => setSub('op')}>Incidencias y antenas</button>
        <button className="tab" aria-current={sub === 'an' ? 'page' : undefined} onClick={() => setSub('an')}>Analítica</button>
      </nav>
      {sub === 'an' && <Analitica api={api} zonas={zonas} />}
      {sub === 'op' && <>
      <div className="fila entre">
        <Resumen api={api} version={version} />
        <button className="btn btn-sec" onClick={() => setVersion((v) => v + 1)}>Actualizar datos</button>
      </div>
      <Tabla api={api} zonas={zonas} version={version} onCambio={() => setVersion((v) => v + 1)} />
      <Antenas api={api} zonas={zonas} version={version} />
      </>}
    </>
  );
}
