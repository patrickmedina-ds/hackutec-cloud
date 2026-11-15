const ALFABETO = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~';

export function base64Url(bytes: Uint8Array): string {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** Cadena aleatoria con el alfabeto no reservado de RFC 7636 (43 a 128 caracteres). */
export function cadenaAleatoria(largo = 64): string {
  const bytes = crypto.getRandomValues(new Uint8Array(largo));
  let s = '';
  for (const b of bytes) s += ALFABETO[b % ALFABETO.length];
  return s;
}

export async function desafioS256(verifier: string): Promise<string> {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(hash));
}

export async function crearPkce(): Promise<{ verifier: string; challenge: string; state: string }> {
  const verifier = cadenaAleatoria(64);
  return { verifier, challenge: await desafioS256(verifier), state: cadenaAleatoria(32) };
}

export function normalizarDominio(dominio: string): string {
  const d = dominio.trim().replace(/\/+$/, '');
  return /^https?:\/\//.test(d) ? d : `https://${d}`;
}

export function urlAutorizacion(
  cfg: { cognitoDomain: string; clientId: string; redirectUri: string },
  challenge: string,
  state: string,
): string {
  const p = new URLSearchParams({
    response_type: 'code',
    client_id: cfg.clientId,
    redirect_uri: cfg.redirectUri,
    scope: 'openid email profile',
    code_challenge_method: 'S256',
    code_challenge: challenge,
    state,
  });
  return `${normalizarDominio(cfg.cognitoDomain)}/oauth2/authorize?${p}`;
}

export function cuerpoToken(
  cfg: { clientId: string; redirectUri: string },
  code: string,
  verifier: string,
): URLSearchParams {
  return new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: cfg.clientId,
    code,
    redirect_uri: cfg.redirectUri,
    code_verifier: verifier,
  });
}
