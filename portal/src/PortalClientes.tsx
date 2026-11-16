import { useRef, useState } from 'react';
import { TIPOS } from './types';
import type { ApiPublica, ReporteAceptado, RespuestaConsulta, ServicioOpcion } from './types';
import { Aviso, EstadoBadge, LineaTiempo, fmtFecha } from './ui';
import {
  RE_DNI, RE_SERVICIO, aReportePublico, ahoraLocal, mensajeError, normalizarIdentificador, serviciosDeError, validarFalla,
} from './publico';
import type { ErroresFalla, FormFalla } from './publico';

const inicial = (): FormFalla => ({ identificador: '', tipo: '', descripcion: '', fechaHoraLocal: ahoraLocal() });

export function ReportarFalla({ api }: { api: ApiPublica }) {
  const [f, setF] = useState<FormFalla>(inicial);
  const [errores, setErrores] = useState<ErroresFalla>({});
  const [enviando, setEnviando] = useState(false);
  const [errEnvio, setErrEnvio] = useState('');
  const [servicios, setServicios] = useState<ServicioOpcion[]>([]);
  const [elegido, setElegido] = useState('');
  const [creado, setCreado] = useState<ReporteAceptado | null>(null);
  const resumen = useRef<HTMLDivElement>(null);

  const set = <K extends keyof FormFalla>(k: K, v: FormFalla[K]) => {
    setF((p) => ({ ...p, [k]: v }));
    if (k === 'identificador') { setServicios([]); setElegido(''); }
  };

  async function enviar(ev: React.FormEvent) {
    ev.preventDefault();
    const e = validarFalla(f);
    setErrores(e);
    setErrEnvio('');
    if (Object.keys(e).length) {
      setTimeout(() => resumen.current?.focus(), 0);
      return;
    }
    if (servicios.length && !elegido) {
      setErrEnvio('Elige cuál de tus servicios tiene la falla.');
      return;
    }
    setEnviando(true);
    try {
      setCreado(await api.reportarFalla(aReportePublico(f, servicios.length ? elegido : undefined)));
      setServicios([]);
      setElegido('');
    } catch (err) {
      const opciones = serviciosDeError(err);
      if (opciones.length) setServicios(opciones);
      setErrEnvio(mensajeError(err));
    } finally {
      setEnviando(false);
    }
  }

  if (creado) {
    return (
      <section className="panel confirmacion" aria-live="polite">
        <h2>Reporte recibido</h2>
        <p>Registramos tu reporte en la zona <strong>{creado.zonaNombre}</strong>. Tu número de incidencia es:</p>
        <p className="codigo" tabIndex={-1}>{creado.incidenciaId}</p>
        <p className="muted">Te avisaremos por correo cuando cambie de estado. También puedes verlo en “Consultar mis reportes”.</p>
        <button className="btn" onClick={() => { setCreado(null); setF(inicial()); setErrores({}); }}>Enviar otro reporte</button>
      </section>
    );
  }

  const campo = (k: keyof FormFalla) => ({
    'aria-invalid': errores[k] ? true : undefined,
    'aria-describedby': errores[k] ? `err-${k}` : undefined,
  });
  const Err = ({ k }: { k: keyof FormFalla }) => (errores[k] ? <p className="error-campo" id={`err-${k}`}>{errores[k]}</p> : null);

  return (
    <form className="panel form" onSubmit={enviar} noValidate>
      <h2>Reportar una falla</h2>
      <p className="muted">
        Solo para clientes de Red TeleCorp. Identifícate con tu DNI o con el número de servicio de tu contrato:
        la zona y tus datos de contacto los tomamos de tu contrato.
      </p>
      {Object.keys(errores).length > 0 && (
        <div className="aviso aviso-error" role="alert" tabIndex={-1} ref={resumen}>
          Revisa {Object.keys(errores).length === 1 ? 'el campo marcado' : 'los campos marcados'} antes de enviar.
        </div>
      )}
      <div className="grid2">
        <div className="campo">
          <label htmlFor="ident">DNI o número de servicio *</label>
          <input id="ident" autoComplete="off" placeholder="70000001 o SRV-00000001" value={f.identificador} onChange={(e) => set('identificador', e.target.value)} {...campo('identificador')} />
          <Err k="identificador" />
        </div>
        <div className="campo">
          <label htmlFor="fecha">¿Cuándo ocurrió? *</label>
          <input id="fecha" type="datetime-local" max={ahoraLocal()} value={f.fechaHoraLocal} onChange={(e) => set('fechaHoraLocal', e.target.value)} {...campo('fechaHoraLocal')} />
          <Err k="fechaHoraLocal" />
        </div>
      </div>

      {servicios.length > 0 && (
        <fieldset className="campo">
          <legend>¿Cuál de tus servicios tiene la falla? *</legend>
          {servicios.map((s) => (
            <label key={s.numeroServicio} className="fila">
              <input type="radio" name="servicio" value={s.numeroServicio} checked={elegido === s.numeroServicio} onChange={() => setElegido(s.numeroServicio)} />
              <span><span className="mono">{s.numeroServicio}</span> · {s.zonaNombre}</span>
            </label>
          ))}
        </fieldset>
      )}

      <div className="campo">
        <label htmlFor="tipo">Tipo de problema *</label>
        <select id="tipo" value={f.tipo} onChange={(e) => set('tipo', e.target.value as FormFalla['tipo'])} {...campo('tipo')}>
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

      {errEnvio && <Aviso tipo={servicios.length ? 'info' : 'error'}>{errEnvio}</Aviso>}
      <button className="btn" type="submit" disabled={enviando}>{enviando ? 'Enviando…' : 'Enviar reporte'}</button>
    </form>
  );
}

export function ConsultarReportes({ api }: { api: ApiPublica }) {
  const [dni, setDni] = useState('');
  const [servicio, setServicio] = useState('');
  const [err, setErr] = useState('');
  const [cargando, setCargando] = useState(false);
  const [res, setRes] = useState<RespuestaConsulta | null>(null);

  async function consultar(ev: React.FormEvent) {
    ev.preventDefault();
    setErr('');
    const d = dni.trim();
    const s = normalizarIdentificador(servicio);
    if (!RE_DNI.test(d) || !RE_SERVICIO.test(s)) {
      setErr('Escribe tu DNI (8 dígitos) y tu número de servicio (SRV-12345678).');
      return;
    }
    setCargando(true);
    try {
      setRes(await api.consultarReportes(d, s));
    } catch (e) {
      setRes(null);
      setErr(mensajeError(e));
    } finally {
      setCargando(false);
    }
  }

  return (
    <section className="panel">
      <form className="form" onSubmit={consultar} noValidate>
        <h2>Consultar mis reportes</h2>
        <p className="muted">Por seguridad pedimos ambos datos de tu contrato.</p>
        <div className="grid2">
          <div className="campo">
            <label htmlFor="c-dni">DNI *</label>
            <input id="c-dni" inputMode="numeric" autoComplete="off" value={dni} onChange={(e) => setDni(e.target.value)} />
          </div>
          <div className="campo">
            <label htmlFor="c-srv">Número de servicio *</label>
            <input id="c-srv" autoComplete="off" placeholder="SRV-00000001" value={servicio} onChange={(e) => setServicio(e.target.value)} />
          </div>
        </div>
        {err && <Aviso tipo="error">{err}</Aviso>}
        <button className="btn" type="submit" disabled={cargando}>{cargando ? 'Buscando…' : 'Consultar'}</button>
      </form>
      {res && (
        <div aria-live="polite">
          <p className="muted">Servicio {res.servicio} · {res.zonaNombre}</p>
          {res.reportes.length === 0 && <p className="muted">Este servicio no tiene reportes.</p>}
          <ul className="lista">
            {res.reportes.map((r) => (
              <li key={r.incidenciaId} className="tarjeta">
                <div className="tarjeta-cab">
                  <span className="tarjeta-tit">{TIPOS.find((t) => t.valor === r.tipo)?.etiqueta ?? r.tipo}</span>
                  <EstadoBadge estado={r.estado} />
                  <span className="muted tarjeta-fecha">{fmtFecha(r.creadoEn)}</span>
                </div>
                <div className="tarjeta-cuerpo">
                  <p className="muted mono">Incidencia {r.incidenciaId}</p>
                  {r.descripcion && <p>{r.descripcion}</p>}
                  <LineaTiempo filas={r.historial} />
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
