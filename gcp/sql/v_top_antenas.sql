-- Antenas con mas degradaciones (cambios a un estado distinto de NORMAL).
-- Ordenar por degradaciones DESC en el grafico (una vista no garantiza orden).
SELECT
  antenaId,
  ANY_VALUE(zonaId)                         AS zonaId,
  COUNT(*)                                  AS degradaciones,
  COUNTIF(estadoNuevo = 'CAIDA')            AS caidas,
  COUNTIF(estadoNuevo = 'SATURADA')         AS saturaciones,
  COUNTIF(estadoNuevo = 'INHABILITADA')     AS inhabilitaciones,
  MAX(ts)                                   AS ultima_degradacion
FROM `${proyecto}.${dataset}.v_antenas_eventos`
WHERE degradada
GROUP BY antenaId
