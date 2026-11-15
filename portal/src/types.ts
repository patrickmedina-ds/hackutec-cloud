export type Estado = 'REGISTRADA' | 'EN_REVISION' | 'ATENDIDA' | 'SOLUCIONADA' | 'CERRADA';
export const ESTADOS: Estado[] = ['REGISTRADA', 'EN_REVISION', 'ATENDIDA', 'SOLUCIONADA', 'CERRADA'];

export type TipoProblema =
  | 'SIN_INTERNET'
  | 'SIN_LLAMADAS'
  | 'SIN_SMS'
  | 'LENTITUD'
  | 'INTERMITENCIA'
  | 'OTRO';
export const TIPOS: { valor: TipoProblema; etiqueta: string }[] = [
  { valor: 'SIN_INTERNET', etiqueta: 'Sin internet' },
  { valor: 'SIN_LLAMADAS', etiqueta: 'No puedo hacer o recibir llamadas' },
  { valor: 'SIN_SMS', etiqueta: 'No llegan o no salen SMS' },
  { valor: 'LENTITUD', etiqueta: 'Internet muy lento' },
  { valor: 'INTERMITENCIA', etiqueta: 'Servicio intermitente' },
  { valor: 'OTRO', etiqueta: 'Otro problema' },
];

export type Clase = 'INDIVIDUAL' | 'COLECTIVA';
export type Origen = 'USUARIO' | 'ANTENA' | 'RECURRENCIA';
export type Severidad = 'BAJA' | 'MEDIA' | 'ALTA' | 'CRITICA';
export type Nivel = 'P1' | 'P2' | 'P3' | 'P4';
export type EstadoAntena = 'NORMAL' | 'CAIDA' | 'SATURADA' | 'INHABILITADA';
export type Rol = 'operador' | 'usuario';

export interface NuevoReporte {
  documento: string;
  numeroServicio: string;
  telefono?: string;
  email?: string;
  zonaId: string;
  lat?: number;
  lon?: number;
  fechaHoraEvento: string;
  tipo: TipoProblema;
  descripcion: string;
}

export interface Incidencia {
  incidenciaId: string;
  clase: Clase;
  origen: Origen;
  estado: Estado;
  zonaId: string;
  tipo: TipoProblema;
  descripcion: string;
  fechaHoraEvento: string;
  tipoIA?: string | null;
  severidadIA?: Severidad | null;
  resumenIA?: string | null;
  prioridad?: number | null;
  nivelPrioridad?: Nivel | null;
  incidenteColectivoId?: string | null;
  usuariosAfectados?: number | null;
  antenasAfectadas?: string[] | null;
  creadoEn: string;
  actualizadoEn: string;
  resueltoEn?: string | null;
}

export interface Transicion {
  estadoAnterior?: Estado | null;
  estadoNuevo: Estado;
  actor: string;
  comentario?: string | null;
  ts: string;
}

export interface Zona {
  zonaId: string;
  nombre: string;
  tipoZona: string;
  ciudad: string;
  lat: number;
  lon: number;
}

export interface Antena {
  antenaId: string;
  zonaId: string;
  estado: EstadoAntena;
  gbPorMinuto: number;
  ultimaLectura: string;
}

export interface FiltrosIncidencias {
  zonaId?: string;
  estado?: Estado | '';
  clase?: Clase | '';
  desde?: string;
  limite?: number;
  cursor?: string | null;
}

export interface Pagina {
  items: Incidencia[];
  cursor: string | null;
}

export type Reporte = 'top_zonas' | 'top_antenas' | 'tipos_problema' | 'mttr' | 'colectivos' | 'recurrencia';
export type Dias = 7 | 30 | 90;

export interface FilaTopZona { zonaId: string; incidencias: number; abiertas: number; prioridad_alta: number }
export interface FilaTopAntena { antenaId: string; zonaId: string; degradaciones: number; caidas: number; saturaciones: number; inhabilitaciones: number; ultima_degradacion: string }
export interface FilaTipo { tipo: string; incidencias: number }
export interface FilaMttr { zonaId: string; nivelPrioridad: Nivel; resueltas: number; mttr_min: number; mediana_min: number; maximo_min: number }
export interface FilaColectivo { incidenteColectivoId: string; zonaId: string; usuariosDistintos: number; totalIncidencias: number; detectado_en: string }
export interface FilaRecurrencia { zonaId: string; colectivos: number; primer_colectivo: string; ultimo_colectivo: string; es_recurrente: boolean }
export interface FilasPorReporte {
  top_zonas: FilaTopZona; top_antenas: FilaTopAntena; tipos_problema: FilaTipo;
  mttr: FilaMttr; colectivos: FilaColectivo; recurrencia: FilaRecurrencia;
}
export interface RespuestaAnalitica<R extends Reporte = Reporte> {
  reporte: R; dias: number; total: number; filas: FilasPorReporte[R][]; cache: boolean;
}

/** Contrato común del cliente real y del modo demostración. */
export interface Api {
  crearReporte(r: NuevoReporte): Promise<{ incidenciaId: string; estado: 'REGISTRADA' }>;
  listarIncidencias(f?: FiltrosIncidencias): Promise<Pagina>;
  obtenerIncidencia(id: string): Promise<Incidencia>;
  historial(id: string): Promise<Transicion[]>;
  cambiarEstado(id: string, estadoNuevo: Estado, comentario?: string): Promise<Incidencia>;
  listarZonas(): Promise<Zona[]>;
  listarAntenas(f?: { zonaId?: string; estado?: EstadoAntena | '' }): Promise<Antena[]>;
  analitica<R extends Reporte>(reporte: R, dias?: number): Promise<RespuestaAnalitica<R>>;
  /** Solo demo: cambia el rol activo. */
  setRolDemo?(rol: Rol): void;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public codigo: string,
    mensaje: string,
  ) {
    super(mensaje);
    this.name = 'ApiError';
  }
}

export interface Config {
  apiUrl: string;
  region: string;
  cognitoDomain: string;
  clientId: string;
  redirectUri: string;
}
