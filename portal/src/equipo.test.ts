import { describe, expect, it, vi } from 'vitest';
import { crearApi } from './api';
import { EMAIL_DEMO, crearDemo } from './demo';
import { validarEmail } from './Equipo';
import { urlAutorizacion } from './pkce';
import { ApiError } from './types';

const cfg = { cognitoDomain: 'tc.auth.us-east-1.amazoncognito.com', clientId: 'cid', redirectUri: 'https://app.test/' };

function setup(status: number, cuerpo?: unknown) {
  const f = vi.fn(async () => new Response(cuerpo === undefined ? null : JSON.stringify(cuerpo), { status }));
  return { f, api: crearApi('https://api.test/dev/', () => 'ID.TOKEN', f as unknown as typeof fetch) };
}

describe('cliente API de operadores', () => {
  it('lista con GET /operadores', async () => {
    const { f, api } = setup(200, [{ email: 'a@b.co', creado: 'x', estado: 'CONFIRMED' }]);
    expect(await api.listarOperadores()).toHaveLength(1);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://api.test/dev/operadores');
    expect(init.method).toBe('GET');
  });

  it('promueve con POST y cuerpo {email}', async () => {
    const { f, api } = setup(200, { email: 'a@b.co', creado: 'x', estado: 'CONFIRMED' });
    await api.promoverOperador('a@b.co');
    const [, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ email: 'a@b.co' });
  });

  it('quita con DELETE y escapa el correo', async () => {
    const { f, api } = setup(200, { email: 'a@b.co', creado: 'x', estado: 'CONFIRMED' });
    await api.quitarOperador('a+1@b.co');
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://api.test/dev/operadores/a%2B1%40b.co');
    expect(init.method).toBe('DELETE');
  });

  it('propaga 404 y 409 como ApiError con el mensaje del servidor', async () => {
    const { api } = setup(404, { codigo: 'USUARIO_NO_ENCONTRADO', mensaje: 'El usuario debe crear su cuenta primero' });
    await expect(api.promoverOperador('x@y.co')).rejects.toMatchObject({ status: 404, message: 'El usuario debe crear su cuenta primero' });
    const r = setup(409, { codigo: 'ULTIMO_OPERADOR', mensaje: 'No se puede quitar al último operador' });
    await expect(r.api.quitarOperador('x@y.co')).rejects.toBeInstanceOf(ApiError);
  });
});

describe('modo demo: operadores', () => {
  it('lista, promueve a un cliente existente y lo quita', async () => {
    const d = crearDemo('operador');
    expect((await d.listarOperadores()).map((o) => o.email)).toContain(EMAIL_DEMO);
    const op = await d.promoverOperador(' Maria.Cliente@correo.com ');
    expect(op.email).toBe('maria.cliente@correo.com');
    expect(await d.listarOperadores()).toHaveLength(3);
    await d.quitarOperador('maria.cliente@correo.com');
    expect(await d.listarOperadores()).toHaveLength(2);
  });

  it('404 si no tiene cuenta, 400 si el correo es inválido', async () => {
    const d = crearDemo('operador');
    await expect(d.promoverOperador('nadie@correo.com')).rejects.toMatchObject({ status: 404 });
    await expect(d.promoverOperador('malo')).rejects.toMatchObject({ status: 400 });
  });

  it('409 al quitarse a sí mismo y 404 si no es operador', async () => {
    const d = crearDemo('operador');
    await expect(d.quitarOperador(EMAIL_DEMO)).rejects.toMatchObject({ status: 409, codigo: 'AUTO_REVOCACION' });
    await expect(d.quitarOperador('jorge.cliente@correo.com')).rejects.toMatchObject({ status: 404 });
  });

  it('409 al quitar al último operador', async () => {
    const d = crearDemo('operador');
    await d.quitarOperador('luis.turno@telecorp.pe');
    await d.promoverOperador('jorge.cliente@correo.com');
    await d.quitarOperador('jorge.cliente@correo.com');
    // solo queda el operador demo (uno mismo): protegido por autorrevocación
    await expect(d.quitarOperador(EMAIL_DEMO)).rejects.toMatchObject({ status: 409 });
  });

  it('un usuario normal recibe 403', async () => {
    const d = crearDemo('usuario');
    await expect(d.listarOperadores()).rejects.toMatchObject({ status: 403 });
    await expect(d.promoverOperador('maria.cliente@correo.com')).rejects.toMatchObject({ status: 403 });
  });
});

describe('formulario y registro', () => {
  it('valida el correo', () => {
    expect(validarEmail('')).not.toBe('');
    expect(validarEmail('sin-arroba')).not.toBe('');
    expect(validarEmail(' a@b.co ')).toBe('');
  });

  it('la URL de registro usa /signup con el mismo PKCE y state', () => {
    const u = new URL(urlAutorizacion(cfg, 'CH', 'ST', 'signup'));
    expect(u.origin + u.pathname).toBe('https://tc.auth.us-east-1.amazoncognito.com/signup');
    expect(u.searchParams.get('client_id')).toBe('cid');
    expect(u.searchParams.get('response_type')).toBe('code');
    expect(u.searchParams.get('scope')).toBe('openid email profile');
    expect(u.searchParams.get('code_challenge')).toBe('CH');
    expect(u.searchParams.get('state')).toBe('ST');
    expect(urlAutorizacion(cfg, 'CH', 'ST')).toContain('/oauth2/authorize?');
  });
});
