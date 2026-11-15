-- Una fila por cambio de estado de antena (Antena.CambioEstado).
-- degradada = TRUE cuando la antena pasa a un estado distinto de NORMAL.
SELECT
  ce_id,
  JSON_VALUE(detalle, '$.antenaId')               AS antenaId,
  JSON_VALUE(detalle, '$.zonaId')                 AS zonaId,
  JSON_VALUE(detalle, '$.estadoAnterior')         AS estadoAnterior,
  JSON_VALUE(detalle, '$.estadoNuevo')            AS estadoNuevo,
  SAFE_CAST(JSON_VALUE(detalle, '$.gbPorMinuto') AS FLOAT64) AS gbPorMinuto,
  COALESCE(SAFE.TIMESTAMP(JSON_VALUE(detalle, '$.ts')), ce_time) AS ts,
  JSON_VALUE(detalle, '$.estadoNuevo') != 'NORMAL' AS degradada
FROM `${proyecto}.${dataset}.v_eventos`
WHERE ce_type = 'Antena.CambioEstado'
