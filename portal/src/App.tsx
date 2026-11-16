import { useEffect, useMemo, useRef, useState } from 'react';
import { cerrarSesion, completarCallback, crearCuenta, iniciarSesion, leerSesion } from './auth';
import type { Sesion } from './auth';
import { crearApi, crearApiPublica } from './api';
import { esModoDemo } from './config';
import { EMAIL_DEMO, crearDemo } from './demo';
import { OperadorVista } from './Operador';
import { crearDemoPublica } from './publico';
import { ConsultarReportes, ReportarFalla } from './PortalClientes';
import type { Config } from './types';
import { Aviso } from './ui';

type VistaPublica = 'reportar' | 'consultar';
type ModoDemo = 'publico' | 'operador';

function useTema() {
  const [tema, setTema] = useState<'claro' | 'oscuro' | 'auto'>(() => {
    try {
      const t = localStorage.getItem('telecorp.tema');
      return t === 'claro' || t === 'oscuro' ? t : 'auto';
    } catch {
      return 'auto';
    }
  });
  useEffect(() => {
    const r = document.documentElement;
    if (tema === 'auto') r.removeAttribute('data-theme');
    else r.setAttribute('data-theme', tema === 'claro' ? 'light' : 'dark');
    try {
      if (tema === 'auto') localStorage.removeItem('telecorp.tema');
      else localStorage.setItem('telecorp.tema', tema);
    } catch { /* sin almacenamiento */ }
  }, [tema]);
  const ciclo = () => setTema((t) => (t === 'auto' ? 'claro' : t === 'claro' ? 'oscuro' : 'auto'));
  return [tema, ciclo] as const;
}

export function App({ cfg }: { cfg: Config }) {
  const demo = esModoDemo(cfg);
  const [tema, ciclarTema] = useTema();
  const [sesion, setSesion] = useState<Sesion | null>(() => (demo ? null : leerSesion()));
  const [cargandoAuth, setCargandoAuth] = useState(!demo);
  const [errAuth, setErrAuth] = useState('');
  const [modoDemo, setModoDemo] = useState<ModoDemo>('publico');
  const [vistaPublica, setVistaPublica] = useState<VistaPublica>('reportar');
  const [acceso, setAcceso] = useState(false); // pantalla "Acceso operadores"
  const sesionRef = useRef<Sesion | null>(sesion);
  sesionRef.current = sesion;

  useEffect(() => {
    if (demo) return;
    completarCallback(cfg)
      .then((s) => s && setSesion(s))
      .catch((e) => { setErrAuth(e.message); setAcceso(true); })
      .finally(() => setCargandoAuth(false));
  }, [cfg, demo]);

  const api = useMemo(
    () => (demo ? crearDemo('operador') : crearApi(cfg.apiUrl, () => sesionRef.current?.idToken ?? null)),
    [cfg, demo],
  );
  const apiPublica = useMemo(() => (demo ? crearDemoPublica() : crearApiPublica(cfg.apiUrl)), [cfg, demo]);

  const esOperador = demo ? modoDemo === 'operador' : sesion?.rol === 'operador';

  // Token vencido: volver a la página pública
  useEffect(() => {
    if (demo || !sesion) return;
    const ms = sesion.exp * 1000 - Date.now();
    const t = setTimeout(() => setSesion(null), Math.max(ms, 0));
    return () => clearTimeout(t);
  }, [demo, sesion]);

  const cabecera = (
    <header className="cab">
      <div className="marca">
        <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" fill="currentColor" /><path d="M16 7v18M9 12a10 10 0 0 1 14 0M12 16a6 6 0 0 1 8 0" stroke="var(--fondo)" strokeWidth="2.4" fill="none" strokeLinecap="round" /></svg>
        <div><strong>Red TeleCorp</strong><span>Portal de incidencias</span></div>
      </div>
      <div className="cab-acc">
        {demo && (
          <label className="sel-rol">Vista
            <select value={modoDemo} onChange={(e) => setModoDemo(e.target.value as ModoDemo)}>
              <option value="publico">Cliente (público)</option>
              <option value="operador">Operador</option>
            </select>
          </label>
        )}
        <button className="btn btn-sec" onClick={ciclarTema} aria-label={`Tema: ${tema}. Cambiar tema`}>
          Tema: {tema === 'auto' ? 'automático' : tema}
        </button>
        {!demo && sesion && (
          <>
            <span className="muted usuario">{sesion.email ?? 'Sesión activa'} · {sesion.rol}</span>
            <button className="btn btn-sec" onClick={() => cerrarSesion(cfg)}>Salir</button>
          </>
        )}
        {!demo && !sesion && !cargandoAuth && (
          acceso
            ? <button className="btn btn-sec" onClick={() => setAcceso(false)}>Volver al portal de clientes</button>
            : <button className="btn btn-sec" onClick={() => setAcceso(true)}>Acceso operadores</button>
        )}
      </div>
    </header>
  );

  const bandaDemo = demo && (
    <div className="banda-demo" role="note">
      <strong>MODO DEMOSTRACIÓN</strong> · Datos de ejemplo en memoria. Clientes de prueba: DNI 70000001 (un servicio),
      70000002 (dos servicios), 70000004 (suspendido); para consultar usa DNI 70000001 y SRV-00000001.
    </div>
  );

  const paginaPublica = (
    <>
      <nav className="tabs" aria-label="Secciones">
        {([['reportar', 'Reportar falla'], ['consultar', 'Consultar mis reportes']] as [VistaPublica, string][]).map(([v, t]) => (
          <button key={v} className="tab" aria-current={vistaPublica === v ? 'page' : undefined} onClick={() => setVistaPublica(v)}>{t}</button>
        ))}
      </nav>
      {vistaPublica === 'reportar' ? <ReportarFalla api={apiPublica} /> : <ConsultarReportes api={apiPublica} />}
    </>
  );

  let cuerpo: React.ReactNode;
  if (!demo && cargandoAuth) {
    cuerpo = <p className="centro muted">Cargando…</p>;
  } else if (esOperador) {
    cuerpo = (
      <>
        <nav className="tabs" aria-label="Secciones">
          <button className="tab" aria-current="page">Panel de operación</button>
        </nav>
        <OperadorVista api={api} miEmail={demo ? EMAIL_DEMO : sesion?.email} />
      </>
    );
  } else if (!demo && !sesion && acceso) {
    const incompleta = !cfg.cognitoDomain || !cfg.clientId || !cfg.redirectUri;
    cuerpo = (
      <section className="panel ingreso">
        <h1>Acceso operadores</h1>
        <p>Ingreso exclusivo para el personal de Red TeleCorp. Los clientes no necesitan cuenta para reportar una falla.</p>
        {errAuth && <Aviso tipo="error">{errAuth}</Aviso>}
        {incompleta && <Aviso tipo="error">Falta configuración de Cognito en config.json.</Aviso>}
        <div className="fila acciones-ingreso">
          <button className="btn" disabled={incompleta} onClick={() => iniciarSesion(cfg)}>Iniciar sesión</button>
        </div>
        <p className="muted">Personal de Red TeleCorp: crea tu cuenta y pide acceso a un operador.</p>
        <div className="fila acciones-ingreso">
          <button className="btn btn-sec" disabled={incompleta} onClick={() => crearCuenta(cfg)}>Crear cuenta</button>
        </div>
        <p className="muted">¿Solo quieres ver cómo funciona? Abre la <a href="?demo=1">demostración con datos de ejemplo</a>.</p>
      </section>
    );
  } else {
    cuerpo = (
      <>
        {!demo && sesion && (
          <Aviso tipo="info">Tu cuenta aún no tiene acceso de operador. Pide a un operador que te lo dé desde la pestaña Equipo.</Aviso>
        )}
        {paginaPublica}
      </>
    );
  }

  return (
    <>
      <a className="saltar" href="#principal">Saltar al contenido</a>
      {bandaDemo}
      {cabecera}
      <main id="principal">{cuerpo}</main>
      <footer className="pie">Red TeleCorp · {demo ? 'datos de ejemplo' : `región ${cfg.region}`}</footer>
    </>
  );
}
