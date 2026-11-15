-- Recurrencia: zonas con varios incidentes colectivos e incidencias creadas
-- por la deteccion de recurrencia (origen = 'RECURRENCIA').
SELECT
  z.zonaId,
  z.colectivos,
  z.primer_colectivo,
  z.ultimo_colectivo,
  COALESCE(r.incidencias_recurrencia, 0) AS incidencias_recurrencia,
  z.colectivos > 1                       AS es_recurrente
FROM (
  SELECT
    zonaId,
    COUNT(DISTINCT incidenteColectivoId) AS colectivos,
    MIN(detectado_en)                    AS primer_colectivo,
    MAX(detectado_en)                    AS ultimo_colectivo
  FROM `${proyecto}.${dataset}.v_colectivos`
  GROUP BY zonaId
) AS z
LEFT JOIN (
  SELECT zonaId, COUNT(*) AS incidencias_recurrencia
  FROM `${proyecto}.${dataset}.v_incidencias`
  WHERE origen = 'RECURRENCIA'
  GROUP BY zonaId
) AS r ON r.zonaId = z.zonaId
