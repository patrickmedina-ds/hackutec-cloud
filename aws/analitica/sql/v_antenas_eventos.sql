-- Una fila por cambio de estado de antena (Antena.CambioEstado).
-- degradada = TRUE cuando la antena pasa a un estado distinto de NORMAL.
SELECT
  ce_id,
  detalle.antenaId                                       AS antenaId,
  detalle.zonaId                                         AS zonaId,
  detalle.estadoAnterior                                 AS estadoAnterior,
  detalle.estadoNuevo                                    AS estadoNuevo,
  detalle.gbPorMinuto                                    AS gbPorMinuto,
  COALESCE(TRY(CAST(from_iso8601_timestamp(detalle.ts) AS timestamp)), ce_time) AS ts,
  detalle.estadoNuevo <> 'NORMAL'                        AS degradada
FROM {{db}}.v_eventos
WHERE ce_type = 'Antena.CambioEstado'
