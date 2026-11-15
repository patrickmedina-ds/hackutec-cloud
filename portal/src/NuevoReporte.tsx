import { useEffect, useRef, useState } from 'react';
import { TIPOS } from './types';
import type { Api, Zona } from './types';
import { aReporte, ahoraLocal, validar } from './validacion';
import type { Errores, FormReporte } from './validacion';
import { Aviso } from './ui';

const inicial = (): FormReporte => ({
  documento: '', numeroServicio: '', telefono: '', email: '', zonaId: '',
  fechaHoraLocal: ahoraLocal(), tipo: '', descripcion: '',
});

export function NuevoReporteVista({ api, onCreado }: { api: Api; onCreado: () => void }) {
  const [f, setF] = useState<FormReporte>(inicial);
  const [zonas, setZonas] = useState<Zona[]>([]);
  const [errZonas, setErrZonas] = useState('');
  const [errores, setErrores] = useState<Errores>({});
  const [enviando, setEnviando] = useState(false);
  const [errEnvio, setErrEnvio] = useState('');
  const [creado, setCreado] = useState<string | null>(null);
  const [geo, setGeo] = useState<{ estado: 'idle' | 'buscando' | 'error'; msg?: string }>({ estado: 'idle' });
  const resumen = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.listarZonas().then(setZonas).catch((e) => setErrZonas(e.message));
  }, [api]);

  const set = <K extends keyof FormReporte>(k: K, v: FormReporte[K]) => setF((p) => ({ ...p, [k]: v }));

  function pedirUbicacion() {
    if (!navigator.geolocation) return setGeo({ estado: 'error', msg: 'Tu navegador no permite geolocalización.' });
    setGeo({ estado: 'buscando' });
    navigator.geolocation.getCurrentPosition(
      (p) => {
        setF((x) => ({ ...x, lat: +p.coords.latitude.toFixed(6), lon: +p.coords.longitude.toFixed(6) }));
        setGeo({ estado: 'idle' });
      },
      () => setGeo({ estado: 'error', msg: 'No se pudo obtener la ubicación. Puedes enviar el reporte sin ella.' }),
      { timeout: 10_000 },
    );
  }

  async function enviar(ev: React.FormEvent) {
    ev.preventDefault();
    const e = validar(f);
    setErrores(e);
    setErrEnvio('');
    if (Object.keys(e).length) {
      setTimeout(() => resumen.current?.focus(), 0);
      return;
    }
    setEnviando(true);
    try {
      const r = await api.crearReporte(aReporte(f));
      setCreado(r.incidenciaId);
      onCreado();
    } catch (err) {
      setErrEnvio(err instanceof Error ? err.message : 'No se pudo enviar el reporte.');
    } finally {
      setEnviando(false);
    }
  }

  if (creado) {
    return (
      <section className="panel confirmacion" aria-live="polite">
        <h2>Reporte recibido</h2>
        <p>Lo registramos y lo estamos analizando. Guarda este código para seguirlo:</p>
        <p className="codigo" tabIndex={-1}>{creado}</p>
        <p className="muted">Puedes ver su avance en la pestaña “Mis reportes”.</p>
        <button className="btn" onClick={() => { setCreado(null); setF(inicial()); setErrores({}); }}>
          Enviar otro reporte
        </button>
      </section>
    );
  }

  const campo = (k: keyof FormReporte) => ({
    'aria-invalid': errores[k] ? true : undefined,
    'aria-describedby': errores[k] ? `err-${k}` : undefined,
  });
  const Err = ({ k }: { k: keyof FormReporte }) =>
    errores[k] ? <p className="error-campo" id={`err-${k}`}>{errores[k]}</p> : null;

  return (
    <form className="panel form" onSubmit={enviar} noValidate>
      <h2>Reportar un problema</h2>
      <p className="muted">Los campos con * son obligatorios.</p>

      {Object.keys(errores).length > 0 && (
        <div className="aviso aviso-error" role="alert" tabIndex={-1} ref={resumen}>
          Revisa {Object.keys(errores).length === 1 ? 'el campo marcado' : 'los campos marcados'} antes de enviar.
        </div>
      )}
      {errZonas && <Aviso tipo="error">No se pudieron cargar las zonas: {errZonas}</Aviso>}

      <div className="grid2">
        <div className="campo">
          <label htmlFor="documento">Documento de identidad *</label>
          <input id="documento" autoComplete="off" inputMode="numeric" value={f.documento} onChange={(e) => set('documento', e.target.value)} {...campo('documento')} />
          <Err k="documento" />
        </div>
        <div className="campo">
          <label htmlFor="servicio">Número de servicio *</label>
          <input id="servicio" autoComplete="off" value={f.numeroServicio} onChange={(e) => set('numeroServicio', e.target.value)} {...campo('numeroServicio')} />
          <Err k="numeroServicio" />
        </div>
        <div className="campo">
          <label htmlFor="telefono">Teléfono de contacto</label>
          <input id="telefono" type="tel" autoComplete="tel" placeholder="+51987654321" value={f.telefono} onChange={(e) => set('telefono', e.target.value)} {...campo('telefono')} />
          <Err k="telefono" />
        </div>
        <div className="campo">
          <label htmlFor="email">Correo electrónico</label>
          <input id="email" type="email" autoComplete="email" value={f.email} onChange={(e) => set('email', e.target.value)} {...campo('email')} />
          <Err k="email" />
        </div>
        <div className="campo">
          <label htmlFor="zona">Zona *</label>
          <select id="zona" value={f.zonaId} onChange={(e) => set('zonaId', e.target.value)} {...campo('zonaId')}>
            <option value="">Elige una zona…</option>
            {zonas.map((z) => <option key={z.zonaId} value={z.zonaId}>{z.nombre} · {z.ciudad}</option>)}
          </select>
          <Err k="zonaId" />
        </div>
        <div className="campo">
          <label htmlFor="fecha">¿Cuándo ocurrió? *</label>
          <input id="fecha" type="datetime-local" max={ahoraLocal()} value={f.fechaHoraLocal} onChange={(e) => set('fechaHoraLocal', e.target.value)} {...campo('fechaHoraLocal')} />
          <Err k="fechaHoraLocal" />
        </div>
      </div>

      <div className="campo">
        <label htmlFor="tipo">Tipo de problema *</label>
        <select id="tipo" value={f.tipo} onChange={(e) => set('tipo', e.target.value as FormReporte['tipo'])} {...campo('tipo')}>
          <option value="">Elige una opción…</option>
          {TIPOS.map((t) => <option key={t.valor} value={t.valor}>{t.etiqueta}</option>)}
        </select>
        <Err k="tipo" />
      </div>

      <div className="campo">
        <label htmlFor="desc">Descripción * <span className="muted">({f.descripcion.length}/1000)</span></label>
        <textarea id="desc" rows={4} maxLength={1100} value={f.descripcion} onChange={(e) => set('descripcion', e.target.value)} {...campo('descripcion')} />
        <Err k="descripcion" />
      </div>

      <div className="campo geo">
        <span className="etiqueta">Ubicación (opcional)</span>
        <div className="fila">
          <button type="button" className="btn btn-sec" onClick={pedirUbicacion} disabled={geo.estado === 'buscando'}>
            {geo.estado === 'buscando' ? 'Buscando…' : f.lat !== undefined ? 'Actualizar ubicación' : 'Usar mi ubicación'}
          </button>
          {f.lat !== undefined && f.lon !== undefined && (
            <>
              <span className="muted">{f.lat}, {f.lon}</span>
              <button type="button" className="btn-link" onClick={() => setF((x) => ({ ...x, lat: undefined, lon: undefined }))}>Quitar</button>
            </>
          )}
        </div>
        {geo.msg && <p className="error-campo">{geo.msg}</p>}
      </div>

      {errEnvio && <Aviso tipo="error">{errEnvio}</Aviso>}
      <button className="btn" type="submit" disabled={enviando}>{enviando ? 'Enviando…' : 'Enviar reporte'}</button>
    </form>
  );
}
