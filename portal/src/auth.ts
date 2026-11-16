import { crearPkce, cuerpoToken, normalizarDominio, urlAutorizacion } from './pkce';
import type { Config, Rol } from './types';

export interface Sesion {
  idToken: string;
  exp: number; // epoch segundos
  email?: string;
  sub?: string;
  grupos: string[];
  rol: Rol;
}

const K_SESION = 'telecorp.sesion';
const K_VERIFIER = 'telecorp.pkce.verifier';
const K_STATE = 'telecorp.pkce.state';

export function decodificarJwt(token: string): Record<string, unknown> {
  const parte = token.split('.')[1];
  if (!parte) throw new Error('Token con formato inválido');
  const b64 = parte.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(parte.length / 4) * 4, '=');
  const bin = atob(b64);
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}

export function rolDesdeGrupos(grupos: string[]): Rol {
  return grupos.includes('operadores') ? 'operador' : 'usuario';
}

export function sesionDesdeIdToken(idToken: string): Sesion {
  const c = decodificarJwt(idToken);
  const g = c['cognito:groups'];
  const grupos = Array.isArray(g) ? g.map(String) : typeof g === 'string' ? [g] : [];
  return {
    idToken,
    exp: Number(c.exp ?? 0),
    email: typeof c.email === 'string' ? c.email : undefined,
    sub: typeof c.sub === 'string' ? c.sub : undefined,
    grupos,
    rol: rolDesdeGrupos(grupos),
  };
}

export function sesionVigente(s: Sesion | null, ahoraMs = Date.now()): boolean {
  return !!s && s.exp * 1000 > ahoraMs + 5000;
}

export function leerSesion(): Sesion | null {
  try {
    const raw = sessionStorage.getItem(K_SESION);
    if (!raw) return null;
    const s = JSON.parse(raw) as Sesion;
    return sesionVigente(s) ? s : null;
  } catch {
    return null;
  }
}

function guardarSesion(s: Sesion) {
  try {
    sessionStorage.setItem(K_SESION, JSON.stringify(s));
  } catch {
    /* sin almacenamiento: la sesión vive solo en memoria */
  }
}

async function irACognito(cfg: Config, ruta: 'oauth2/authorize' | 'signup'): Promise<void> {
  const { verifier, challenge, state } = await crearPkce();
  sessionStorage.setItem(K_VERIFIER, verifier);
  sessionStorage.setItem(K_STATE, state);
  window.location.assign(urlAutorizacion(cfg, challenge, state, ruta));
}

export const iniciarSesion = (cfg: Config) => irACognito(cfg, 'oauth2/authorize');

/** Registro de Cognito (managed login) con el mismo PKCE/state que el login. */
export const crearCuenta = (cfg: Config) => irACognito(cfg, 'signup');

/** Si la URL trae ?code=..., canjea el código por tokens. Devuelve la sesión o null. */
export async function completarCallback(cfg: Config, fetchImpl: typeof fetch = (...a) => fetch(...a)): Promise<Sesion | null> {
  const params = new URLSearchParams(window.location.search);
  const code = params.get('code');
  const error = params.get('error');
  if (error) {
    limpiarUrl();
    throw new Error(params.get('error_description') ?? error);
  }
  if (!code) return null;
  const state = params.get('state');
  const verifier = sessionStorage.getItem(K_VERIFIER);
  const esperado = sessionStorage.getItem(K_STATE);
  limpiarUrl();
  if (!verifier || !state || state !== esperado) {
    throw new Error('La respuesta de inicio de sesión no coincide con la solicitud. Inténtalo de nuevo.');
  }
  const resp = await fetchImpl(`${normalizarDominio(cfg.cognitoDomain)}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: cuerpoToken(cfg, code, verifier),
  });
  sessionStorage.removeItem(K_VERIFIER);
  sessionStorage.removeItem(K_STATE);
  if (!resp.ok) throw new Error('Cognito rechazó el código de autorización.');
  const t = (await resp.json()) as { id_token?: string };
  if (!t.id_token) throw new Error('Cognito no devolvió id_token.');
  const sesion = sesionDesdeIdToken(t.id_token);
  guardarSesion(sesion);
  return sesion;
}

function limpiarUrl() {
  const u = new URL(window.location.href);
  for (const k of ['code', 'state', 'error', 'error_description']) u.searchParams.delete(k);
  window.history.replaceState({}, '', u.pathname + u.search + u.hash);
}

export function cerrarSesion(cfg: Config) {
  try {
    sessionStorage.removeItem(K_SESION);
  } catch {
    /* nada */
  }
  const p = new URLSearchParams({ client_id: cfg.clientId, logout_uri: cfg.redirectUri });
  window.location.assign(`${normalizarDominio(cfg.cognitoDomain)}/logout?${p}`);
}
