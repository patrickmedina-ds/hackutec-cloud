import { useCallback, useEffect, useState } from 'react';
import type { Api, Incidencia, Transicion, Zona } from './types';
import { Aviso, EstadoBadge, LineaTiempo, fmtFecha, nombreZona } from './ui';
import { TIPOS } from './types';

function Tarjeta({ inc, api, zonas }: { inc: Incidencia; api: Api; zonas: Zona[] }) {
  const [abierto, setAbierto] = useState(false);
  const [hist, setHist] = useState<Transicion[] | null>(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (!abierto || hist) return;
    api.historial(inc.incidenciaId).then(setHist).catch((e) => setErr(e.message));
  }, [abierto, hist, api, inc.incidenciaId]);

  return (
    <li className="tarjeta">
      <button className="tarjeta-cab" aria-expanded={abierto} onClick={() => setAbierto(!abierto)}>
        <span className="tarjeta-tit">
          {TIPOS.find((t) => t.valor === inc.tipo)?.etiqueta ?? inc.tipo}
          <span className="muted"> · {nombreZona(zonas, inc.zonaId)}</span>
        </span>
        <EstadoBadge estado={inc.estado} />
        <span className="muted tarjeta-fecha">{fmtFecha(inc.creadoEn)}</span>
      </button>
      {abierto && (
        <div className="tarjeta-cuerpo">
          <p className="muted mono">Código {inc.incidenciaId}</p>
          <p>{inc.descripcion}</p>
          {err && <Aviso tipo="error">{err}</Aviso>}
          {hist ? <LineaTiempo filas={hist} /> : !err && <p className="muted">Cargando historial…</p>}
        </div>
      )}
    </li>
  );
}

export function MisReportes({ api, version }: { api: Api; version: number }) {
  const [items, setItems] = useState<Incidencia[] | null>(null);
  const [zonas, setZonas] = useState<Zona[]>([]);
  const [err, setErr] = useState('');

  const cargar = useCallback(() => {
    setErr('');
    Promise.all([api.listarIncidencias({ limite: 100 }), api.listarZonas()])
      .then(([p, z]) => { setItems(p.items); setZonas(z); })
      .catch((e) => setErr(e.message));
  }, [api]);
  useEffect(cargar, [cargar, version]);

  return (
    <section className="panel">
      <div className="fila entre">
        <h2>Mis reportes</h2>
        <button className="btn btn-sec" onClick={cargar}>Actualizar</button>
      </div>
      {err && <Aviso tipo="error">{err}</Aviso>}
      {!items && !err && <p className="muted">Cargando…</p>}
      {items && items.length === 0 && <p className="muted">Todavía no has enviado reportes.</p>}
      <ul className="lista">
        {items?.map((i) => <Tarjeta key={i.incidenciaId} inc={i} api={api} zonas={zonas} />)}
      </ul>
    </section>
  );
}
