-- Incidentes por zona (abiertas o no) y colectivos detectados.
SELECT
  i.zonaId,
  count(*)                                                        AS incidencias,
  count_if(i.estado_actual NOT IN ('SOLUCIONADA', 'CERRADA'))     AS abiertas,
  count_if(i.nivelPrioridad IN ('P1', 'P2'))                      AS prioridad_alta,
  COALESCE(max(c.colectivos), 0)                                  AS colectivos
FROM {{db}}.v_incidencias AS i
LEFT JOIN (
  SELECT zonaId, count(DISTINCT incidenteColectivoId) AS colectivos
  FROM {{db}}.v_colectivos
  GROUP BY zonaId
) AS c ON c.zonaId = i.zonaId
GROUP BY i.zonaId
