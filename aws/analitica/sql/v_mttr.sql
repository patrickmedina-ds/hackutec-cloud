-- Tiempo medio de resolucion (MTTR) por zona y nivel de prioridad.
-- Solo incidencias que ya llegaron a SOLUCIONADA.
SELECT
  zonaId,
  COALESCE(nivelPrioridad, 'SIN_PRIORIDAD')  AS nivelPrioridad,
  count(*)                                   AS resueltas,
  round(avg(duracion_min), 1)                AS mttr_min,
  approx_percentile(duracion_min, 0.5)       AS mediana_min,
  max(duracion_min)                          AS maximo_min
FROM {{db}}.v_incidencias
WHERE duracion_min IS NOT NULL
GROUP BY 1, 2
