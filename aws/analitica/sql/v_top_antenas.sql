-- Antenas con mas degradaciones (cambios a un estado distinto de NORMAL).
-- Ordenar por degradaciones DESC en la consulta (una vista no garantiza orden).
SELECT
  antenaId,
  arbitrary(zonaId)                                      AS zonaId,
  count(*)                                               AS degradaciones,
  count_if(estadoNuevo = 'CAIDA')                        AS caidas,
  count_if(estadoNuevo = 'SATURADA')                     AS saturaciones,
  count_if(estadoNuevo = 'INHABILITADA')                 AS inhabilitaciones,
  max(ts)                                                AS ultima_degradacion
FROM {{db}}.v_antenas_eventos
WHERE degradada
GROUP BY antenaId
