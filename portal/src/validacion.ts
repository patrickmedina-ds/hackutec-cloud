import type { NuevoReporte, TipoProblema } from './types';
import { TIPOS } from './types';

export interface FormReporte {
  documento: string;
  numeroServicio: string;
  telefono: string;
  email: string;
  zonaId: string;
  lat?: number;
  lon?: number;
  fechaHoraLocal: string; // valor de <input type="datetime-local">
  tipo: TipoProblema | '';
  descripcion: string;
}

export type Errores = Partial<Record<keyof FormReporte, string>>;

export function ahoraLocal(d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function validar(f: FormReporte, ahora = new Date()): Errores {
  const e: Errores = {};
  const len = (s: string) => s.trim().length;
  if (len(f.documento) < 6 || len(f.documento) > 20) e.documento = 'El documento debe tener entre 6 y 20 caracteres.';
  if (len(f.numeroServicio) < 6 || len(f.numeroServicio) > 20) e.numeroServicio = 'El número de servicio debe tener entre 6 y 20 caracteres.';
  if (f.telefono.trim() && !/^\+[1-9]\d{7,14}$/.test(f.telefono.trim())) e.telefono = 'Usa el formato internacional, por ejemplo +51987654321.';
  if (f.email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(f.email.trim())) e.email = 'Escribe un correo válido.';
  if (!f.zonaId) e.zonaId = 'Elige tu zona.';
  if (!f.tipo || !TIPOS.some((t) => t.valor === f.tipo)) e.tipo = 'Elige el tipo de problema.';
  const fecha = new Date(f.fechaHoraLocal);
  if (!f.fechaHoraLocal || isNaN(fecha.getTime())) e.fechaHoraLocal = 'Indica cuándo ocurrió.';
  else if (fecha.getTime() > ahora.getTime() + 60_000) e.fechaHoraLocal = 'La fecha no puede estar en el futuro.';
  if (!len(f.descripcion)) e.descripcion = 'Cuéntanos qué está pasando.';
  else if (f.descripcion.length > 1000) e.descripcion = 'Máximo 1000 caracteres.';
  return e;
}

export function aReporte(f: FormReporte): NuevoReporte {
  const r: NuevoReporte = {
    documento: f.documento.trim(),
    numeroServicio: f.numeroServicio.trim(),
    zonaId: f.zonaId,
    fechaHoraEvento: new Date(f.fechaHoraLocal).toISOString().replace(/\.\d+Z$/, 'Z'),
    tipo: f.tipo as TipoProblema,
    descripcion: f.descripcion.trim(),
  };
  if (f.telefono.trim()) r.telefono = f.telefono.trim();
  if (f.email.trim()) r.email = f.email.trim();
  if (f.lat !== undefined && f.lon !== undefined) {
    r.lat = f.lat;
    r.lon = f.lon;
  }
  return r;
}
