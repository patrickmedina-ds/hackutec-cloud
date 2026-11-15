-- Capa base: eventos deduplicados por id (Firehose/EventBridge entregan al menos una vez).
-- La fila es el sobre de EventBridge; detail (struct) pasa a llamarse detalle.
SELECT
  id                                                    AS ce_id,
  detail_type                                           AS ce_type,
  source                                                AS ce_source,
  TRY(CAST(from_iso8601_timestamp("time") AS timestamp)) AS ce_time,
  detail                                                AS detalle
FROM (
  SELECT
    e.*,
    row_number() OVER (PARTITION BY id ORDER BY "time") AS rn
  FROM {{db}}.eventos AS e
  WHERE id IS NOT NULL
)
WHERE rn = 1
