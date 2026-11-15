-- Incidentes colectivos de los tres orígenes: USUARIO (umbral de 10 reportes), ANTENA
-- (detección proactiva) y RECURRENCIA. Una fila por incidencia COLECTIVA con su último estado.
WITH col AS (
  SELECT
    incidenciaId                                         AS incidenteColectivoId,
    max_by(zonaId, ts)                                   AS zonaId,
    max_by(origen, ts)                                   AS origen,
    max_by(estadoNuevo, ts)                              AS estado,
    max_by(nivelPrioridad, ts)                           AS nivelPrioridad,
    min(ts)                                              AS detectado_en,
    min(CASE WHEN estadoNuevo = 'SOLUCIONADA' THEN ts END) AS resuelto_en
  FROM {{db}}.v_transiciones
  WHERE clase = 'COLECTIVA'
  GROUP BY incidenciaId
),
hijos AS (
  SELECT incidenteColectivoId, count(DISTINCT incidenciaId) AS totalIncidencias
  FROM {{db}}.v_transiciones
  WHERE clase = 'INDIVIDUAL' AND incidenteColectivoId IS NOT NULL
  GROUP BY incidenteColectivoId
),
umbral AS (
  SELECT detalle.incidenteColectivoId AS incidenteColectivoId, max(detalle.usuariosDistintos) AS usuariosDistintos
  FROM {{db}}.v_eventos
  WHERE ce_type = 'Zona.UmbralSuperado'
  GROUP BY detalle.incidenteColectivoId
)
SELECT
  col.incidenteColectivoId,
  col.zonaId,
  col.origen,
  col.estado,
  col.nivelPrioridad,
  COALESCE(umbral.usuariosDistintos, hijos.totalIncidencias, 0) AS usuariosDistintos,
  COALESCE(hijos.totalIncidencias, 0)                    AS totalIncidencias,
  col.detectado_en,
  col.resuelto_en,
  date_diff('minute', col.detectado_en, col.resuelto_en) AS minutos_hasta_resolver
FROM col
LEFT JOIN hijos  ON hijos.incidenteColectivoId  = col.incidenteColectivoId
LEFT JOIN umbral ON umbral.incidenteColectivoId = col.incidenteColectivoId
