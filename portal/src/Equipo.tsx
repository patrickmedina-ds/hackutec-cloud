import { useCallback, useEffect, useState } from 'react';
import { ApiError } from './types';
import type { Api, Operador } from './types';
import { Aviso, fmtFecha } from './ui';

export const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function validarEmail(valor: string): string {
  const v = valor.trim();
  if (!v) return 'Escribe el correo de la persona.';
  if (v.length > 254 || !EMAIL_RE.test(v)) return 'Escribe un correo válido, por ejemplo nombre@dominio.com.';
  return '';
}

export function Equipo({ api, miEmail }: { api: Api; miEmail?: string }) {
  const [ops, setOps] = useState<Operador[] | null>(null);
  const [errLista, setErrLista] = useState('');
  const [email, setEmail] = useState('');
  const [errCampo, setErrCampo] = useState('');
  const [msg, setMsg] = useState<{ tipo: 'ok' | 'error'; texto: string } | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [confirmar, setConfirmar] = useState<string | null>(null);
  const [quitando, setQuitando] = useState(false);
  const yo = miEmail?.trim().toLowerCase();

  const cargar = useCallback(async () => {
    setErrLista('');
    try {
      setOps(await api.listarOperadores());
    } catch (e) {
      setErrLista(e instanceof Error ? e.message : 'No se pudo cargar el equipo');
    }
  }, [api]);
  useEffect(() => { void cargar(); }, [cargar]);

  async function promover(ev: React.FormEvent) {
    ev.preventDefault();
    setMsg(null);
    const problema = validarEmail(email);
    setErrCampo(problema);
    if (problema) return;
    setEnviando(true);
    try {
      const op = await api.promoverOperador(email.trim());
      setMsg({ tipo: 'ok', texto: `${op.email} ahora es operador.` });
      setEmail('');
      await cargar();
    } catch (e) {
      setMsg({
        tipo: 'error',
        texto: e instanceof ApiError && e.status === 404
          ? 'Ese correo no tiene cuenta. La persona debe crear su cuenta primero (botón "Crear cuenta" en la pantalla de ingreso) y luego volver a intentarlo.'
          : e instanceof Error ? e.message : 'No se pudo promover al usuario',
      });
    } finally {
      setEnviando(false);
    }
  }

  async function quitar(correo: string) {
    setQuitando(true);
    setMsg(null);
    try {
      await api.quitarOperador(correo);
      setMsg({ tipo: 'ok', texto: `${correo} ya no es operador.` });
      setConfirmar(null);
      await cargar();
    } catch (e) {
      setConfirmar(null);
      setMsg({ tipo: 'error', texto: e instanceof Error ? e.message : 'No se pudo quitar al operador' });
    } finally {
      setQuitando(false);
    }
  }

  return (
    <section className="equipo" aria-label="Equipo de operadores">
      <h2>Equipo de operadores</h2>
      {msg && <Aviso tipo={msg.tipo}>{msg.texto}</Aviso>}
      {errLista && <Aviso tipo="error">{errLista}</Aviso>}

      <form className="equipo-form" onSubmit={promover} noValidate aria-label="Promover a operador">
        <h3>Promover a operador</h3>
        <p className="muted">La persona debe haber creado su cuenta antes. Escribe el correo con el que se registró.</p>
        <label htmlFor="equipo-email" className="etiqueta">Correo</label>
        <div className="fila">
          <input
            id="equipo-email" type="email" autoComplete="off" value={email}
            aria-invalid={errCampo ? true : undefined} aria-describedby={errCampo ? 'equipo-email-err' : undefined}
            onChange={(e) => { setEmail(e.target.value); if (errCampo) setErrCampo(''); }}
          />
          <button className="btn" type="submit" disabled={enviando}>{enviando ? 'Promoviendo…' : 'Promover'}</button>
        </div>
        {errCampo && <p id="equipo-email-err" className="campo-error" role="alert">{errCampo}</p>}
      </form>

      <h3>Operadores actuales{ops ? ` (${ops.length})` : ''}</h3>
      {!ops && !errLista && <p className="muted">Cargando…</p>}
      {ops && (
        <div className="scroll-x">
          <table>
            <caption className="visually-hidden">Usuarios del grupo operadores</caption>
            <thead>
              <tr><th scope="col">Correo</th><th scope="col">Nombre</th><th scope="col">Desde</th><th scope="col">Estado</th><th scope="col"><span className="visually-hidden">Acciones</span></th></tr>
            </thead>
            <tbody>
              {ops.map((o) => {
                const soyYo = !!yo && o.email.toLowerCase() === yo;
                return (
                  <tr key={o.email}>
                    <td>{o.email}{soyYo && <> <span className="chip neutro">tú</span></>}</td>
                    <td>{o.nombre ?? '—'}</td>
                    <td>{fmtFecha(o.creado)}</td>
                    <td>{o.estado}</td>
                    <td>
                      {confirmar === o.email ? (
                        <span className="fila confirmar" role="group" aria-label={`Confirmar quitar a ${o.email}`}>
                          <span>¿Quitar acceso de operador a {o.email}?</span>
                          <button className="btn btn-peligro" disabled={quitando} onClick={() => quitar(o.email)}>{quitando ? 'Quitando…' : 'Sí, quitar'}</button>
                          <button className="btn btn-sec" disabled={quitando} onClick={() => setConfirmar(null)}>Cancelar</button>
                        </span>
                      ) : (
                        <button
                          className="btn btn-sec" disabled={soyYo || quitando}
                          title={soyYo ? 'No puedes quitarte a ti mismo' : undefined}
                          aria-label={`Quitar a ${o.email}`}
                          onClick={() => { setMsg(null); setConfirmar(o.email); }}
                        >Quitar</button>
                      )}
                    </td>
                  </tr>
                );
              })}
              {ops.length === 0 && <tr><td colSpan={5} className="muted">No hay operadores.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
