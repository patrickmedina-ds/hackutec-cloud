import { ApiError, TIPOS } from './types';
import type { ApiPublica, Estado, ReporteConsultado, ReportePublico, ServicioOpcion, TipoProblema, Transicion } from './types';

export const RE_DNI = /^\d{8}$/;
export const RE_SERVICIO = /^SRV-\d{8}$/;
export const RE_SERVICIO_ENMASCARADO = /^SRV-\*{4}\d{4}$/;

export function ahoraLocal(d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export interface FormFalla {
  identificador: string; // DNI (8 dígitos) o número de servicio (SRV-12345678)
  tipo: TipoProblema | '';
  descripcion: string;
  fechaHoraLocal: string;
}
export type ErroresFalla = Partial<Record<keyof FormFalla, string>>;

export function normalizarIdentificador(v: string): string {
  return v.trim().toUpperCase().replace(/\s+/g, '');
}

export function validarFalla(f: FormFalla, ahora = new Date()): ErroresFalla {
  const e: ErroresFalla = {};
  const id = normalizarIdentificador(f.identificador);
  if (!RE_DNI.test(id) && !RE_SERVICIO.test(id)) e.identificador = 'Escribe tu DNI (8 dígitos) o tu número de servicio (SRV-12345678).';
  if (!f.tipo || !TIPOS.some((t) => t.valor === f.tipo)) e.tipo = 'Elige el tipo de problema.';
  if (!f.descripcion.trim()) e.descripcion = 'Cuéntanos qué está pasando.';
  else if (f.descripcion.length > 1000) e.descripcion = 'Máximo 1000 caracteres.';
  const fecha = new Date(f.fechaHoraLocal);
  if (!f.fechaHoraLocal || isNaN(fecha.getTime())) e.fechaHoraLocal = 'Indica cuándo ocurrió.';
  else if (fecha.getTime() > ahora.getTime() + 60_000) e.fechaHoraLocal = 'La fecha no puede estar en el futuro.';
  return e;
}

/** Arma el cuerpo de POST /reportes. `servicioElegido` es el número enmascarado tras un 409. */
export function aReportePublico(f: FormFalla, servicioElegido?: string): ReportePublico {
  const id = normalizarIdentificador(f.identificador);
  const r: ReportePublico = {
    tipo: f.tipo as TipoProblema,
    descripcion: f.descripcion.trim(),
    fechaHoraEvento: new Date(f.fechaHoraLocal).toISOString().replace(/\.\d+Z$/, 'Z'),
  };
  if (RE_DNI.test(id)) r.documento = id;
  else r.numeroServicio = id;
  if (servicioElegido) r.numeroServicio = servicioElegido;
  return r;
}

/** Mensaje claro para cada error de la API pública. */
export function mensajeError(e: unknown): string {
  if (!(e instanceof ApiError)) return 'No se pudo enviar el reporte.';
  switch (e.codigo) {
    case 'CLIENTE_NO_ENCONTRADO':
      return 'No encontramos un servicio con esos datos. Revisa tu DNI o número de servicio (figura en tu contrato o recibo).';
    case 'CONTRATO_SUSPENDIDO':
      return 'Tu servicio está suspendido, así que no podemos registrar el reporte. Comunícate con atención al cliente.';
    case 'VARIOS_SERVICIOS':
      return 'Tienes más de un servicio con ese DNI. Elige cuál tiene la falla.';
    default:
      return e.message;
  }
}

export function serviciosDeError(e: unknown): ServicioOpcion[] {
  if (e instanceof ApiError && e.codigo === 'VARIOS_SERVICIOS' && Array.isArray(e.datos.servicios)) {
    return e.datos.servicios as ServicioOpcion[];
  }
  return [];
}

// ---------------------------------------------------------------- modo demo

interface ClienteDemo { documento: string; numeroServicio: string; zonaId: string; zonaNombre: string; activo: boolean }

/** Clientes de ejemplo del modo demo (mismos DNI/servicios de demo que datos/clientes.json). */
export const CLIENTES_DEMO: ClienteDemo[] = [
  { documento: '70000001', numeroServicio: 'SRV-00000001', zonaId: 'LIM-JMA-01', zonaNombre: 'Jesús María · Complejo hospitalario Rebagliati', activo: true },
  { documento: '70000002', numeroServicio: 'SRV-00000002', zonaId: 'LIM-JMA-01', zonaNombre: 'Jesús María · Complejo hospitalario Rebagliati', activo: true },
  { documento: '70000002', numeroServicio: 'SRV-00000003', zonaId: 'LIM-MIR-01', zonaNombre: 'Miraflores · Malecón y Larcomar', activo: true },
  { documento: '70000004', numeroServicio: 'SRV-00000004', zonaId: 'LIM-JMA-01', zonaNombre: 'Jesús María · Complejo hospitalario Rebagliati', activo: false },
];

const enmascarar = (n: string) => `SRV-****${n.slice(-4)}`;
const ahoraIso = () => new Date().toISOString().replace(/\.\d+Z$/, 'Z');
const espera = (ms = 200) => new Promise((r) => setTimeout(r, ms));
const NO_ENCONTRADO = () => new ApiError(404, 'CLIENTE_NO_ENCONTRADO', 'No encontramos un servicio con esos datos');

export function crearDemoPublica(clientes: ClienteDemo[] = CLIENTES_DEMO, retardo = 200): ApiPublica {
  const reportes = new Map<string, ReporteConsultado[]>();
  let n = 0;
  // Un reporte previo del cliente demo, ya en revisión
  const ts = new Date(Date.now() - 3 * 3600_000).toISOString().replace(/\.\d+Z$/, 'Z');
  const ts2 = new Date(Date.now() - 2 * 3600_000).toISOString().replace(/\.\d+Z$/, 'Z');
  reportes.set('SRV-00000001', [{
    incidenciaId: 'demo-previo-1', estado: 'EN_REVISION', zonaId: 'LIM-JMA-01', tipo: 'LENTITUD',
    descripcion: 'Internet lento por la tarde.', creadoEn: ts, actualizadoEn: ts2,
    historial: [
      { estadoAnterior: null, estadoNuevo: 'REGISTRADA', actor: 'SISTEMA', comentario: 'Reporte recibido', ts },
      { estadoAnterior: 'REGISTRADA', estadoNuevo: 'EN_REVISION', actor: 'OPERADOR', comentario: 'Revisando la antena de la zona', ts: ts2 },
    ],
  }]);

  function resolver(r: ReportePublico): ClienteDemo {
    const doc = r.documento;
    const num = r.numeroServicio?.toUpperCase();
    let c: ClienteDemo | undefined;
    if (num && RE_SERVICIO.test(num)) {
      c = clientes.find((x) => x.numeroServicio === num && (!doc || x.documento === doc));
      if (!c) throw NO_ENCONTRADO();
    } else {
      if (!doc) throw new ApiError(400, 'IDENTIFICACION_REQUERIDA', 'Indica tu DNI o tu número de servicio');
      let xs = clientes.filter((x) => x.documento === doc);
      if (num) xs = xs.filter((x) => x.numeroServicio.endsWith(num.slice(-4)));
      if (!xs.length) throw NO_ENCONTRADO();
      if (xs.length > 1) {
        throw new ApiError(409, 'VARIOS_SERVICIOS', 'Tienes varios servicios: elige cuál tiene la falla', {
          servicios: xs.map((x) => ({ numeroServicio: enmascarar(x.numeroServicio), zonaId: x.zonaId, zonaNombre: x.zonaNombre })),
        });
      }
      c = xs[0];
    }
    if (!c.activo) throw new ApiError(403, 'CONTRATO_SUSPENDIDO', 'Tu servicio está suspendido');
    return c;
  }

  return {
    async reportarFalla(r) {
      await espera(retardo);
      const c = resolver(r);
      const id = `demo-${(++n).toString().padStart(4, '0')}-${Math.random().toString(36).slice(2, 8)}`;
      const ts = ahoraIso();
      const fila: Transicion = { estadoAnterior: null, estadoNuevo: 'REGISTRADA' as Estado, actor: 'SISTEMA', comentario: 'Reporte recibido', ts };
      const lista = reportes.get(c.numeroServicio) ?? [];
      lista.unshift({ incidenciaId: id, estado: 'REGISTRADA', zonaId: c.zonaId, tipo: r.tipo, descripcion: r.descripcion, fechaHoraEvento: r.fechaHoraEvento, creadoEn: ts, actualizadoEn: ts, historial: [fila] });
      reportes.set(c.numeroServicio, lista);
      return { incidenciaId: id, estado: 'REGISTRADA', zonaId: c.zonaId, zonaNombre: c.zonaNombre };
    },
    async consultarReportes(documento, numeroServicio) {
      await espera(retardo);
      const num = numeroServicio.trim().toUpperCase();
      const c = clientes.find((x) => x.documento === documento.trim() && x.numeroServicio === num);
      if (!c) throw NO_ENCONTRADO();
      return { servicio: enmascarar(num), zonaId: c.zonaId, zonaNombre: c.zonaNombre, reportes: structuredClone(reportes.get(num) ?? []) };
    },
  };
}
