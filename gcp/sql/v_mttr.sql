-- Tiempo medio de resolucion (MTTR) por zona y nivel de prioridad.
-- Solo incidencias que ya llegaron a SOLUCIONADA.
SELECT
  zonaId,
  COALESCE(nivelPrioridad, 'SIN_PRIORIDAD')       AS nivelPrioridad,
  COUNT(*)                                        AS resueltas,
  ROUND(AVG(duracion_min), 1)                     AS mttr_min,
  APPROX_QUANTILES(duracion_min, 100)[OFFSET(50)] AS mediana_min,
  MAX(duracion_min)                               AS maximo_min
FROM `${proyecto}.${dataset}.v_incidencias`
WHERE duracion_min IS NOT NULL
GROUP BY 1, 2
