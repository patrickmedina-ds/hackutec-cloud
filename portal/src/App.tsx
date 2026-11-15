import { useEffect, useMemo, useRef, useState } from 'react';
import { cerrarSesion, completarCallback, iniciarSesion, leerSesion } from './auth';
import type { Sesion } from './auth';
import { crearApi } from './api';
import { esModoDemo } from './config';
import { crearDemo } from './demo';
import { MisReportes } from './MisReportes';
import { NuevoReporteVista } from './NuevoReporte';
import { OperadorVista } from './Operador';
import type { Config, Rol } from './types';
import { Aviso } from './ui';

type Vista = 'panel' | 'nuevo' | 'mis';

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
  const [rolDemo, setRolDemo] = useState<Rol>('operador');
  const [vista, setVista] = useState<Vista | null>(null);
  const [version, setVersion] = useState(0);
  const sesionRef = useRef<Sesion | null>(sesion);
  sesionRef.current = sesion;

  useEffect(() => {
    if (demo) return;
    completarCallback(cfg)
      .then((s) => s && setSesion(s))
      .catch((e) => setErrAuth(e.message))
      .finally(() => setCargandoAuth(false));
  }, [cfg, demo]);

  const api = useMemo(
    () => (demo ? crearDemo('operador') : crearApi(cfg.apiUrl, () => sesionRef.current?.idToken ?? null)),
    [cfg, demo],
  );

  const rol: Rol | null = demo ? rolDemo : sesion?.rol ?? null;
  const vistaActual: Vista = vista ?? (rol === 'operador' ? 'panel' : 'nuevo');

  // Token vencido: volver a la pantalla de ingreso
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
          <label className="sel-rol">Rol
            <select value={rolDemo} onChange={(e) => { const r = e.target.value as Rol; setRolDemo(r); api.setRolDemo?.(r); setVista(null); setVersion((v) => v + 1); }}>
              <option value="operador">Operador</option>
              <option value="usuario">Usuario</option>
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
      </div>
    </header>
  );

  const bandaDemo = demo && (
    <div className="banda-demo" role="note">
      <strong>MODO DEMOSTRACIÓN</strong> · Datos de ejemplo en memoria, no hay conexión con la red real. Los cambios se pierden al recargar.
    </div>
  );

  let cuerpo: React.ReactNode;
  if (!demo && cargandoAuth) {
    cuerpo = <p className="centro muted">Verificando sesión…</p>;
  } else if (!demo && !sesion) {
    const incompleta = !cfg.cognitoDomain || !cfg.clientId || !cfg.redirectUri;
    cuerpo = (
      <section className="panel ingreso">
        <h1>Bienvenido</h1>
        <p>Inicia sesión para reportar un problema de tu servicio o, si eres operador, gestionar incidencias.</p>
        {errAuth && <Aviso tipo="error">{errAuth}</Aviso>}
        {incompleta && <Aviso tipo="error">Falta configuración de Cognito en config.json.</Aviso>}
        <button className="btn" disabled={incompleta} onClick={() => iniciarSesion(cfg)}>Iniciar sesión</button>
        <p className="muted">¿Solo quieres ver cómo funciona? Abre la <a href="?demo=1">demostración con datos de ejemplo</a>.</p>
      </section>
    );
  } else {
    const tabs: [Vista, string][] = [
      ...(rol === 'operador' ? ([['panel', 'Panel de operación']] as [Vista, string][]) : []),
      ['nuevo', 'Nuevo reporte'],
      ['mis', 'Mis reportes'],
    ];
    cuerpo = (
      <>
        <nav className="tabs" aria-label="Secciones">
          {tabs.map(([v, t]) => (
            <button key={v} className="tab" aria-current={vistaActual === v ? 'page' : undefined} onClick={() => setVista(v)}>{t}</button>
          ))}
        </nav>
        {vistaActual === 'panel' && rol === 'operador' && <OperadorVista key={`${rolDemo}-${version}`} api={api} />}
        {vistaActual === 'nuevo' && <NuevoReporteVista api={api} onCreado={() => setVersion((v) => v + 1)} />}
        {vistaActual === 'mis' && <MisReportes api={api} version={version} />}
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
