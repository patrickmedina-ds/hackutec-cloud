-- Incidentes por zona (abiertas o no) y colectivos detectados.
SELECT
  i.zonaId,
  COUNT(*)                                                    AS incidencias,
  COUNTIF(i.estado_actual NOT IN ('SOLUCIONADA', 'CERRADA')) AS abiertas,
  COUNTIF(i.nivelPrioridad IN ('P1', 'P2'))                   AS prioridad_alta,
  COALESCE(ANY_VALUE(c.colectivos), 0)                        AS colectivos
FROM `${proyecto}.${dataset}.v_incidencias` AS i
LEFT JOIN (
  SELECT zonaId, COUNT(DISTINCT incidenteColectivoId) AS colectivos
  FROM `${proyecto}.${dataset}.v_colectivos`
  GROUP BY zonaId
) AS c ON c.zonaId = i.zonaId
GROUP BY i.zonaId
