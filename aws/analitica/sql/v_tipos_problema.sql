-- Incidencias nuevas (registro inicial) por tipo de problema y dia.
SELECT
  CAST(ts AS date)                  AS dia,
  COALESCE(tipo, 'SIN_TIPO')        AS tipo,
  clase,
  origen,
  count(DISTINCT incidenciaId)      AS incidencias
FROM {{db}}.v_transiciones
WHERE estadoNuevo = 'REGISTRADA'
GROUP BY 1, 2, 3, 4
