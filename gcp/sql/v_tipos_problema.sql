-- Incidencias nuevas (registro inicial) por tipo de problema y dia.
SELECT
  DATE(ts)                   AS dia,
  COALESCE(tipo, 'SIN_TIPO') AS tipo,
  clase,
  origen,
  COUNT(DISTINCT incidenciaId) AS incidencias
FROM `${proyecto}.${dataset}.v_transiciones`
WHERE estadoNuevo = 'REGISTRADA'
GROUP BY 1, 2, 3, 4
