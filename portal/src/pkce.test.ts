import { describe, expect, it } from 'vitest';
import { base64Url, cadenaAleatoria, crearPkce, cuerpoToken, desafioS256, urlAutorizacion } from './pkce';

const cfg = { cognitoDomain: 'mi-dominio.auth.us-east-1.amazoncognito.com', clientId: 'abc123', redirectUri: 'https://x.cloudfront.net/' };

describe('PKCE', () => {
  it('calcula S256 según el vector de prueba de RFC 7636', async () => {
    expect(await desafioS256('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk')).toBe('E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM');
  });
  it('base64url no usa +, / ni =', () => {
    expect(base64Url(new Uint8Array([251, 255, 254]))).toBe('-__-');
  });
  it('genera verifier de 43 a 128 caracteres no reservados', async () => {
    const { verifier, challenge, state } = await crearPkce();
    expect(verifier).toMatch(/^[A-Za-z0-9\-._~]{43,128}$/);
    expect(challenge).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(state.length).toBeGreaterThan(16);
    expect(cadenaAleatoria(64)).not.toBe(cadenaAleatoria(64));
  });
  it('arma la URL de autorización', () => {
    const u = new URL(urlAutorizacion(cfg, 'CHAL', 'EST'));
    expect(u.origin + u.pathname).toBe('https://mi-dominio.auth.us-east-1.amazoncognito.com/oauth2/authorize');
    expect(Object.fromEntries(u.searchParams)).toEqual({
      response_type: 'code', client_id: 'abc123', redirect_uri: 'https://x.cloudfront.net/',
      scope: 'openid email profile', code_challenge_method: 'S256', code_challenge: 'CHAL', state: 'EST',
    });
  });
  it('respeta un dominio que ya trae https://', () => {
    expect(urlAutorizacion({ ...cfg, cognitoDomain: 'https://d.example.com/' }, 'c', 's')).toMatch(/^https:\/\/d\.example\.com\/oauth2\/authorize\?/);
  });
  it('arma el cuerpo del canje de código', () => {
    expect(Object.fromEntries(cuerpoToken(cfg, 'CODE', 'VER'))).toEqual({
      grant_type: 'authorization_code', client_id: 'abc123', code: 'CODE', redirect_uri: 'https://x.cloudfront.net/', code_verifier: 'VER',
    });
  });
});
