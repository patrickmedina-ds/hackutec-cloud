import { ETIQUETA_ESTADO } from './transiciones';
import { ESTADOS } from './types';
import type { Estado, EstadoAntena, Nivel, Severidad, Transicion, Zona } from './types';

export const fmtFecha = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString('es-PE', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—';

export const nombreZona = (zonas: Zona[], id: string) => zonas.find((z) => z.zonaId === id)?.nombre ?? id;

const GLIFO_ESTADO: Record<Estado, string> = { REGISTRADA: '○', EN_REVISION: '◔', ATENDIDA: '◑', SOLUCIONADA: '✓', CERRADA: '■' };
export function EstadoBadge({ estado }: { estado: Estado }) {
  return (
    <span className={`badge est-${estado}`}>
      <span aria-hidden="true">{GLIFO_ESTADO[estado]}</span> {ETIQUETA_ESTADO[estado]}
    </span>
  );
}

const GLIFO_NIVEL: Record<Nivel, string> = { P1: '▲▲', P2: '▲', P3: '◆', P4: '▽' };
const TXT_NIVEL: Record<Nivel, string> = { P1: 'crítica', P2: 'alta', P3: 'media', P4: 'baja' };
export function PrioridadChip({ nivel }: { nivel?: Nivel | null }) {
  if (!nivel) return <span className="muted" title="Aún sin clasificar">—</span>;
  return (
    <span className={`chip niv-${nivel}`} title={`Prioridad ${TXT_NIVEL[nivel]}`}>
      <span aria-hidden="true">{GLIFO_NIVEL[nivel]}</span> {nivel}
    </span>
  );
}

const GLIFO_SEV: Record<Severidad, string> = { BAJA: '▽', MEDIA: '◆', ALTA: '▲', CRITICA: '▲▲' };
export function SeveridadChip({ sev }: { sev?: Severidad | null }) {
  if (!sev) return <span className="muted" title="La IA aún no clasificó">—</span>;
  return (
    <span className={`chip sev-${sev}`} title="Severidad estimada por la IA">
      <span aria-hidden="true">{GLIFO_SEV[sev]}</span> IA {sev.toLowerCase()}
    </span>
  );
}

const GLIFO_ANT: Record<EstadoAntena, string> = { NORMAL: '●', CAIDA: '✕', SATURADA: '▲', INHABILITADA: '⊘' };
const TXT_ANT: Record<EstadoAntena, string> = { NORMAL: 'Normal', CAIDA: 'Caída', SATURADA: 'Saturada', INHABILITADA: 'Inhabilitada' };
export function AntenaBadge({ estado }: { estado: EstadoAntena }) {
  return (
    <span className={`badge ant-${estado}`}>
      <span aria-hidden="true">{GLIFO_ANT[estado]}</span> {TXT_ANT[estado]}
    </span>
  );
}

/** Línea de tiempo: muestra todos los estados posibles y marca los alcanzados con su fecha. */
export function LineaTiempo({ filas }: { filas: Transicion[] }) {
  const alcanzados = new Map<Estado, Transicion>();
  for (const f of filas) alcanzados.set(f.estadoNuevo, f);
  const actual = filas.length ? filas[filas.length - 1].estadoNuevo : null;
  return (
    <ol className="timeline" aria-label="Historial de estados">
      {ESTADOS.map((e) => {
        const t = alcanzados.get(e);
        const clase = e === actual ? 'actual' : t ? 'hecho' : 'pendiente';
        return (
          <li key={e} className={clase} aria-current={e === actual ? 'step' : undefined}>
            <span className="punto" aria-hidden="true">{GLIFO_ESTADO[e]}</span>
            <div>
              <strong>{ETIQUETA_ESTADO[e]}</strong>
              {e === actual && <span className="visually-hidden"> (estado actual)</span>}
              {t ? (
                <>
                  <time dateTime={t.ts}> · {fmtFecha(t.ts)}</time>
                  {t.comentario && <p className="nota">{t.comentario}</p>}
                </>
              ) : (
                <span className="muted"> · pendiente</span>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function Aviso({ tipo, children }: { tipo: 'error' | 'ok' | 'info'; children: React.ReactNode }) {
  return (
    <div className={`aviso aviso-${tipo}`} role={tipo === 'error' ? 'alert' : 'status'}>
      {children}
    </div>
  );
}
