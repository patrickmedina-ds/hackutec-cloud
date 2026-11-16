import { puedeTransicionar } from './transiciones';
import { ApiError } from './types';
import type {
  Antena, Api, Operador, Reporte, RespuestaAnalitica, Estado, FiltrosIncidencias, Incidencia, NuevoReporte, Pagina, Rol, Transicion, TipoProblema, Zona, Nivel, Severidad,
} from './types';

const MIN = 60_000;
const iso = (msAtras: number) => new Date(Date.now() - msAtras).toISOString().replace(/\.\d+Z$/, 'Z');
const espera = (ms = 140) => new Promise((r) => setTimeout(r, ms));

export const ZONAS_DEMO: Zona[] = [
  { zonaId: 'LIM-MIR', nombre: 'Miraflores', tipoZona: 'COMERCIAL', ciudad: 'Lima', lat: -12.1211, lon: -77.0297 },
  { zonaId: 'LIM-SIS', nombre: 'San Isidro', tipoZona: 'COMERCIAL', ciudad: 'Lima', lat: -12.0977, lon: -77.0365 },
  { zonaId: 'LIM-SJL', nombre: 'San Juan de Lurigancho', tipoZona: 'RESIDENCIAL', ciudad: 'Lima', lat: -11.9928, lon: -77.0083 },
  { zonaId: 'LIM-VES', nombre: 'Villa El Salvador', tipoZona: 'RESIDENCIAL', ciudad: 'Lima', lat: -12.2133, lon: -76.9318 },
  { zonaId: 'LIM-CAL', nombre: 'Callao', tipoZona: 'INDUSTRIAL', ciudad: 'Callao', lat: -12.0566, lon: -77.1181 },
  { zonaId: 'LIM-SUR', nombre: 'Santiago de Surco', tipoZona: 'EDUCATIVA', ciudad: 'Lima', lat: -12.1453, lon: -76.9919 },
  { zonaId: 'LIM-LOL', nombre: 'Los Olivos', tipoZona: 'MEDICA', ciudad: 'Lima', lat: -11.9916, lon: -77.0707 },
  { zonaId: 'LIM-ATE', nombre: 'Ate', tipoZona: 'CRITICA', ciudad: 'Lima', lat: -12.0258, lon: -76.9176 },
];

function antenasIniciales(): Antena[] {
  const a = (antenaId: string, zonaId: string, estado: Antena['estado'], gb: number, minAtras = 1): Antena => ({
    antenaId, zonaId, estado, gbPorMinuto: gb, ultimaLectura: iso(minAtras * MIN),
  });
  return [
    a('ANT-MIR-01', 'LIM-MIR', 'NORMAL', 3.2),
    a('ANT-MIR-02', 'LIM-MIR', 'NORMAL', 2.8),
    a('ANT-SIS-01', 'LIM-SIS', 'NORMAL', 4.1),
    a('ANT-SJL-01', 'LIM-SJL', 'SATURADA', 11.8),
    a('ANT-SJL-02', 'LIM-SJL', 'NORMAL', 5.4),
    a('ANT-SJL-03', 'LIM-SJL', 'CAIDA', 0, 14),
    a('ANT-VES-01', 'LIM-VES', 'SATURADA', 10.9),
    a('ANT-VES-02', 'LIM-VES', 'NORMAL', 4.7),
    a('ANT-CAL-01', 'LIM-CAL', 'NORMAL', 6.0),
    a('ANT-CAL-02', 'LIM-CAL', 'INHABILITADA', 0, 180),
    a('ANT-SUR-01', 'LIM-SUR', 'NORMAL', 3.9),
    a('ANT-LOL-01', 'LIM-LOL', 'NORMAL', 5.1),
    a('ANT-LOL-02', 'LIM-LOL', 'CAIDA', 0, 6),
    a('ANT-ATE-01', 'LIM-ATE', 'NORMAL', 4.4),
  ];
}

const RUTAS: Record<Estado, Estado[]> = {
  REGISTRADA: ['REGISTRADA'],
  EN_REVISION: ['REGISTRADA', 'EN_REVISION'],
  ATENDIDA: ['REGISTRADA', 'EN_REVISION', 'ATENDIDA'],
  SOLUCIONADA: ['REGISTRADA', 'EN_REVISION', 'SOLUCIONADA'],
  CERRADA: ['REGISTRADA', 'EN_REVISION', 'SOLUCIONADA', 'CERRADA'],
};

const RESUMENES: Record<TipoProblema, string> = {
  SIN_INTERNET: 'El cliente reporta pérdida total de datos móviles; coincide con degradación de antenas en la zona.',
  SIN_LLAMADAS: 'Fallo en cursado de llamadas de voz; posible problema de registro en la celda.',
  SIN_SMS: 'Mensajes de texto no se entregan; sin otros síntomas de red.',
  LENTITUD: 'Velocidad de descarga muy por debajo de lo contratado en horas pico; compatible con saturación.',
  INTERMITENCIA: 'Cortes breves y repetidos de señal; se sugiere revisar enlace de la antena cercana.',
  OTRO: 'Reporte sin patrón técnico claro; requiere revisión manual.',
};

const TIPOS_DEMO: [string, number][] = [
  ['SIN_INTERNET', 41], ['LENTITUD', 33], ['INTERMITENCIA', 22], ['SIN_LLAMADAS', 14], ['SIN_SMS', 6], ['OTRO', 4],
];
const ZONAS_BASE: [string, number, number, number][] = [
  ['LIM-SJL', 38, 9, 14], ['LIM-VES', 27, 6, 9], ['LIM-LOL', 19, 4, 6], ['LIM-CAL', 15, 5, 4],
  ['LIM-ATE', 10, 1, 3], ['LIM-MIR', 6, 0, 1], ['LIM-SIS', 4, 0, 0], ['LIM-SUR', 3, 0, 0],
];
// [antena, zona, caidas, saturaciones, inhabilitaciones] por cada 7 días
const ANTENAS_BASE: [string, string, number, number, number][] = [
  ['ANT-SJL-03', 'LIM-SJL', 5, 1, 1], ['ANT-SJL-01', 'LIM-SJL', 0, 6, 0], ['ANT-VES-01', 'LIM-VES', 0, 5, 0],
  ['ANT-CAL-02', 'LIM-CAL', 1, 0, 3], ['ANT-LOL-02', 'LIM-LOL', 3, 0, 0], ['ANT-VES-02', 'LIM-VES', 0, 1, 0],
];
const sinZ = (s: string) => s.replace('T', ' ').replace('Z', '');

/** Datos de ejemplo escalados al periodo (7 días = base). */
export function analiticaDemo<R extends Reporte>(reporte: R, dias = 7): RespuestaAnalitica<R> {
  const k = dias / 7;
  const e = (n: number) => (n > 0 ? Math.max(Math.round(n * k), 1) : 0);
  let filas: unknown[];
  switch (reporte) {
    case 'top_zonas':
      filas = ZONAS_BASE.map(([zonaId, i, ab, pa]) => ({ zonaId, incidencias: e(i), abiertas: Math.min(e(ab), e(i)), prioridad_alta: Math.min(e(pa), e(i)) }));
      break;
    case 'top_antenas':
      filas = ANTENAS_BASE.map(([antenaId, zonaId, c, s, h]) => {
        const [cc, ss, hh] = [e(c), e(s), e(h)];
        return { antenaId, zonaId, degradaciones: cc + ss + hh, caidas: cc, saturaciones: ss, inhabilitaciones: hh, ultima_degradacion: sinZ(iso(3 * 60 * MIN)) };
      }).sort((x, y) => y.degradaciones - x.degradaciones);
      break;
    case 'tipos_problema':
      filas = TIPOS_DEMO.map(([tipo, n]) => ({ tipo, incidencias: e(n) }));
      break;
    case 'mttr':
      filas = ZONAS_BASE.slice(0, 5).flatMap(([zonaId], z) =>
        (['P1', 'P2', 'P3'] as const).map((nivelPrioridad, p) => {
          const mttr = Math.round((40 + z * 25 + p * 90) * 10) / 10;
          return { zonaId, nivelPrioridad, resueltas: e(11 - z - p), mttr_min: mttr, mediana_min: Math.round(mttr * 0.8), maximo_min: Math.round(mttr * 2.6) };
        })).sort((x, y) => y.mttr_min - x.mttr_min);
      break;
    case 'colectivos':
      filas = ([['LIM-SJL', 23, 31, 90], ['LIM-VES', 17, 22, 400], ['LIM-LOL', 9, 12, 1500], ['LIM-SJL', 14, 18, 3000], ['LIM-CAL', 8, 10, 12000], ['LIM-VES', 11, 15, 40000]] as [string, number, number, number][])
        .filter(([, , , m]) => m <= dias * 24 * 60)
        .map(([zonaId, u, t, m], i) => ({ incidenteColectivoId: `col-demo-${i + 1}`, zonaId, usuariosDistintos: u, totalIncidencias: t, detectado_en: sinZ(iso(m * MIN)) }));
      break;
    default:
      filas = dias < 30
        ? [{ zonaId: 'LIM-SJL', colectivos: 2, primer_colectivo: sinZ(iso(3000 * MIN)), ultimo_colectivo: sinZ(iso(90 * MIN)), es_recurrente: true }]
        : [{ zonaId: 'LIM-SJL', colectivos: 4, primer_colectivo: sinZ(iso(20 * 1440 * MIN)), ultimo_colectivo: sinZ(iso(90 * MIN)), es_recurrente: true },
           { zonaId: 'LIM-VES', colectivos: 2, primer_colectivo: sinZ(iso(12 * 1440 * MIN)), ultimo_colectivo: sinZ(iso(400 * MIN)), es_recurrente: true }];
  }
  return { reporte, dias, total: filas.length, filas: filas as RespuestaAnalitica<R>['filas'], cache: false };
}

/** Correo del operador que usa el modo demo (quien "inició sesión"). */
export const EMAIL_DEMO = 'ana.operadora@telecorp.pe';

export function crearDemo(rolInicial: Rol = 'operador'): Api {
  let rol: Rol = rolInicial;
  const incidencias: Incidencia[] = [];
  const historial = new Map<string, Transicion[]>();
  const propias = new Set<string>(); // reportes del usuario demo
  const antenas = antenasIniciales();
  let contador = 100;
  const operadores: Operador[] = [
    { email: EMAIL_DEMO, nombre: 'Ana Operadora', creado: iso(40 * 1440 * MIN), estado: 'CONFIRMED' },
    { email: 'luis.turno@telecorp.pe', nombre: 'Luis Turno', creado: iso(12 * 1440 * MIN), estado: 'CONFIRMED' },
  ];
  const clientesDemo = new Set(['maria.cliente@correo.com', 'jorge.cliente@correo.com']);

  function sembrarHistorial(inc: Incidencia, creadoHaceMin: number) {
    const ruta = RUTAS[inc.estado];
    const filas: Transicion[] = ruta.map((e, i) => ({
      estadoAnterior: i === 0 ? null : ruta[i - 1],
      estadoNuevo: e,
      actor: i === 0 ? 'SISTEMA' : e === 'EN_REVISION' && inc.origen !== 'USUARIO' ? 'STEPFN:ciclo-vida' : 'OPERADOR:op-demo',
      comentario:
        i === 0 ? 'Reporte recibido' : e === 'EN_REVISION' ? 'Asignada a un operador' : e === 'ATENDIDA' ? 'Cuadrilla en camino' :
        e === 'SOLUCIONADA' ? 'Servicio restablecido' : 'Confirmado con el cliente',
      ts: iso(Math.max(creadoHaceMin - i * 18, 1) * MIN),
    }));
    historial.set(inc.incidenciaId, filas);
  }

  function nuevaInc(p: Partial<Incidencia> & Pick<Incidencia, 'incidenciaId' | 'estado' | 'zonaId' | 'tipo'>, haceMin: number): Incidencia {
    const inc: Incidencia = {
      clase: 'INDIVIDUAL',
      origen: 'USUARIO',
      descripcion: 'Sin servicio desde hace un rato.',
      fechaHoraEvento: iso((haceMin + 5) * MIN),
      tipoIA: p.tipo,
      severidadIA: 'MEDIA',
      resumenIA: RESUMENES[p.tipo],
      prioridad: 50,
      nivelPrioridad: 'P3',
      creadoEn: iso(haceMin * MIN),
      actualizadoEn: iso(Math.max(haceMin - 30, 1) * MIN),
      resueltoEn: p.estado === 'SOLUCIONADA' || p.estado === 'CERRADA' ? iso(Math.max(haceMin - 40, 1) * MIN) : null,
      ...p,
    };
    incidencias.push(inc);
    sembrarHistorial(inc, haceMin);
    return inc;
  }

  // Colectivo en San Juan de Lurigancho con 12 reportes hijos
  const COL = 'COL-01JDEMOSJL0000000000000001';
  nuevaInc({
    incidenciaId: COL, clase: 'COLECTIVA', origen: 'USUARIO', estado: 'EN_REVISION', zonaId: 'LIM-SJL', tipo: 'SIN_INTERNET',
    descripcion: 'Incidente colectivo: 12 usuarios distintos reportaron fallas en 24 h.',
    severidadIA: 'CRITICA', prioridad: 92, nivelPrioridad: 'P1', usuariosAfectados: 12,
    antenasAfectadas: ['ANT-SJL-03', 'ANT-SJL-01'],
    resumenIA: 'Doce reportes en la misma zona en pocas horas, con la antena ANT-SJL-03 caída y ANT-SJL-01 saturada: es un problema de infraestructura, no de equipos de clientes.',
  }, 95);
  const tiposHijos: TipoProblema[] = ['SIN_INTERNET', 'SIN_INTERNET', 'LENTITUD', 'SIN_LLAMADAS', 'INTERMITENCIA', 'SIN_INTERNET', 'LENTITUD', 'SIN_SMS', 'SIN_INTERNET', 'INTERMITENCIA', 'LENTITUD', 'SIN_INTERNET'];
  tiposHijos.forEach((t, i) => {
    const id = `rep-sjl-${String(i + 1).padStart(2, '0')}`;
    nuevaInc({
      incidenciaId: id, estado: 'EN_REVISION', zonaId: 'LIM-SJL', tipo: t, incidenteColectivoId: COL,
      severidadIA: i % 3 === 0 ? 'ALTA' : 'MEDIA', prioridad: 70 + (i % 5), nivelPrioridad: 'P2',
      descripcion: ['Desde las 8 am no tengo datos.', 'Se corta cada pocos minutos.', 'No puedo cargar nada, ni WhatsApp.', 'Las llamadas se caen.'][i % 4],
    }, 90 - i * 6);
    if (i < 3) propias.add(id);
  });

  const P: [string, Estado, string, TipoProblema, Nivel, Severidad, number, string][] = [
    ['rep-lol-01', 'REGISTRADA', 'LIM-LOL', 'SIN_INTERNET', 'P1', 'CRITICA', 88, 'Clínica sin conexión desde hace 20 minutos.'],
    ['rep-mir-01', 'REGISTRADA', 'LIM-MIR', 'LENTITUD', 'P3', 'BAJA', 35, 'Velocidad muy baja por la tarde.'],
    ['rep-sis-01', 'REGISTRADA', 'LIM-SIS', 'SIN_LLAMADAS', 'P2', 'MEDIA', 62, 'No salen llamadas desde la oficina.'],
    ['rep-ves-01', 'EN_REVISION', 'LIM-VES', 'INTERMITENCIA', 'P2', 'ALTA', 66, 'Señal que va y viene en toda la cuadra.'],
    ['rep-cal-01', 'EN_REVISION', 'LIM-CAL', 'SIN_INTERNET', 'P2', 'ALTA', 71, 'Almacén portuario sin datos.'],
    ['rep-ate-01', 'ATENDIDA', 'LIM-ATE', 'SIN_SMS', 'P3', 'MEDIA', 44, 'No llegan códigos de verificación por SMS.'],
    ['rep-sur-01', 'ATENDIDA', 'LIM-SUR', 'LENTITUD', 'P4', 'BAJA', 22, 'Internet lento en el campus.'],
    ['rep-mir-02', 'SOLUCIONADA', 'LIM-MIR', 'INTERMITENCIA', 'P3', 'MEDIA', 40, 'Cortes cortos durante la noche.'],
    ['rep-sis-02', 'SOLUCIONADA', 'LIM-SIS', 'SIN_INTERNET', 'P2', 'ALTA', 58, 'Sin datos en el piso 12.'],
    ['rep-lol-02', 'CERRADA', 'LIM-LOL', 'OTRO', 'P4', 'BAJA', 15, 'Consulta sobre facturación.'],
    ['rep-ves-02', 'CERRADA', 'LIM-VES', 'SIN_LLAMADAS', 'P3', 'MEDIA', 38, 'Llamadas entrantes se cortan.'],
    ['rep-sur-02', 'CERRADA', 'LIM-SUR', 'LENTITUD', 'P4', 'BAJA', 18, 'Lentitud puntual, ya normal.'],
  ];
  P.forEach(([id, estado, zonaId, tipo, nivelPrioridad, severidadIA, prioridad, descripcion], i) => {
    nuevaInc({ incidenciaId: id, estado, zonaId, tipo, nivelPrioridad, severidadIA, prioridad, descripcion }, 30 + i * 47);
    if (['rep-mir-01', 'rep-sis-02', 'rep-lol-02'].includes(id)) propias.add(id);
  });

  // Incidencia proactiva por antena y una de recurrencia
  nuevaInc({
    incidenciaId: 'ANT-01JDEMOLOL0000000000000002', clase: 'COLECTIVA', origen: 'ANTENA', estado: 'REGISTRADA', zonaId: 'LIM-LOL',
    tipo: 'SIN_INTERNET', descripcion: 'Antena ANT-LOL-02 sin lecturas: detección proactiva.', severidadIA: 'ALTA',
    prioridad: 84, nivelPrioridad: 'P1', antenasAfectadas: ['ANT-LOL-02'], usuariosAfectados: null,
  }, 7);
  nuevaInc({
    incidenciaId: 'REC-01JDEMOVES0000000000000003', clase: 'COLECTIVA', origen: 'RECURRENCIA', estado: 'REGISTRADA', zonaId: 'LIM-VES',
    tipo: 'LENTITUD', descripcion: 'Tercera ocurrencia de lentitud en la zona en 30 días.', severidadIA: 'MEDIA',
    prioridad: 64, nivelPrioridad: 'P2', usuariosAfectados: 5,
  }, 120);

  const ordenadas = () => [...incidencias].sort((a, b) => b.creadoEn.localeCompare(a.creadoEn));
  const clonar = <T,>(x: T): T => structuredClone(x);

  function exigirOperador() {
    if (rol !== 'operador') throw new ApiError(403, 'PROHIBIDO', 'Solo los operadores pueden realizar esta acción.');
  }
  function buscar(id: string): Incidencia {
    const i = incidencias.find((x) => x.incidenciaId === id);
    if (!i || (rol !== 'operador' && !propias.has(id))) throw new ApiError(404, 'NO_ENCONTRADA', 'La incidencia no existe.');
    return i;
  }

  return {
    setRolDemo(r) { rol = r; },
    async crearReporte(r: NuevoReporte) {
      await espera(350);
      const id = `demo-${(++contador).toString(36)}${Math.random().toString(36).slice(2, 8)}`;
      const ahora = iso(0);
      const inc: Incidencia = {
        incidenciaId: id, clase: 'INDIVIDUAL', origen: 'USUARIO', estado: 'REGISTRADA', zonaId: r.zonaId, tipo: r.tipo,
        descripcion: r.descripcion, fechaHoraEvento: r.fechaHoraEvento, tipoIA: null, severidadIA: null, resumenIA: null,
        prioridad: null, nivelPrioridad: null, creadoEn: ahora, actualizadoEn: ahora, resueltoEn: null,
      };
      incidencias.push(inc);
      propias.add(id);
      historial.set(id, [{ estadoAnterior: null, estadoNuevo: 'REGISTRADA', actor: 'SISTEMA', comentario: 'Reporte recibido', ts: ahora }]);
      return { incidenciaId: id, estado: 'REGISTRADA' as const };
    },
    async listarIncidencias(f: FiltrosIncidencias = {}): Promise<Pagina> {
      await espera();
      let xs = ordenadas().filter((i) => rol === 'operador' || propias.has(i.incidenciaId));
      if (f.zonaId) xs = xs.filter((i) => i.zonaId === f.zonaId);
      if (f.estado) xs = xs.filter((i) => i.estado === f.estado);
      if (f.clase) xs = xs.filter((i) => i.clase === f.clase);
      if (f.desde) xs = xs.filter((i) => i.creadoEn >= f.desde!);
      const limite = Math.min(f.limite ?? 50, 200);
      const inicio = f.cursor ? Number(atob(f.cursor)) : 0;
      const items = xs.slice(inicio, inicio + limite);
      const sig = inicio + limite;
      return { items: clonar(items), cursor: sig < xs.length ? btoa(String(sig)) : null };
    },
    async obtenerIncidencia(id) { await espera(80); return clonar(buscar(id)); },
    async historial(id) { await espera(80); buscar(id); return clonar(historial.get(id) ?? []); },
    async cambiarEstado(id, estadoNuevo, comentario) {
      await espera(250);
      exigirOperador();
      const inc = buscar(id);
      if (!puedeTransicionar(inc.estado, estadoNuevo)) {
        throw new ApiError(409, 'TRANSICION_INVALIDA', `${inc.estado} → ${estadoNuevo} no está permitido`);
      }
      const ts = iso(0);
      historial.get(id)!.push({ estadoAnterior: inc.estado, estadoNuevo, actor: 'OPERADOR:op-demo', comentario: comentario || null, ts });
      inc.estado = estadoNuevo;
      inc.actualizadoEn = ts;
      if (estadoNuevo === 'SOLUCIONADA') inc.resueltoEn = ts;
      return clonar(inc);
    },
    async analitica(reporte, dias = 7) {
      await espera(200);
      exigirOperador();
      return analiticaDemo(reporte, dias);
    },
    async listarOperadores() {
      await espera();
      exigirOperador();
      return clonar([...operadores].sort((a, b) => a.email.localeCompare(b.email)));
    },
    async promoverOperador(email) {
      await espera();
      exigirOperador();
      const e = email.trim().toLowerCase();
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e)) throw new ApiError(400, 'EMAIL_INVALIDO', 'Indica un correo válido');
      if (!clientesDemo.has(e) && !operadores.some((o) => o.email === e)) {
        throw new ApiError(404, 'USUARIO_NO_ENCONTRADO', 'El usuario debe crear su cuenta primero');
      }
      let op = operadores.find((o) => o.email === e);
      if (!op) {
        op = { email: e, creado: iso(3 * 1440 * MIN), estado: 'CONFIRMED' };
        operadores.push(op);
      }
      return clonar(op);
    },
    async quitarOperador(email) {
      await espera();
      exigirOperador();
      const e = email.trim().toLowerCase();
      if (e === EMAIL_DEMO) throw new ApiError(409, 'AUTO_REVOCACION', 'No puedes quitarte a ti mismo como operador');
      const i = operadores.findIndex((o) => o.email === e);
      if (i < 0) throw new ApiError(404, 'USUARIO_NO_ENCONTRADO', 'Esa persona no es operadora');
      if (operadores.length <= 1) throw new ApiError(409, 'ULTIMO_OPERADOR', 'No se puede quitar al último operador');
      const [q] = operadores.splice(i, 1);
      clientesDemo.add(q.email);
      return clonar(q);
    },
    async listarZonas() { await espera(60); return clonar(ZONAS_DEMO); },
    async listarAntenas(f = {}) {
      await espera();
      exigirOperador();
      return clonar(antenas.filter((a) => (!f.zonaId || a.zonaId === f.zonaId) && (!f.estado || a.estado === f.estado)));
    },
  };
}
