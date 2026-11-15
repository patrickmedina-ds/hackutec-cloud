-- Capa base: eventos deduplicados con el sobre CloudEvent extraido.
-- Pub/Sub entrega al menos una vez, asi que se deduplica por el id del CloudEvent.
-- La columna data contiene el CloudEvent completo; el detail original esta en $.data.
SELECT
  JSON_VALUE(data, '$.id')                       AS ce_id,
  JSON_VALUE(data, '$.type')                     AS ce_type,
  JSON_VALUE(data, '$.source')                   AS ce_source,
  JSON_VALUE(data, '$.subject')                  AS ce_subject,
  SAFE.TIMESTAMP(JSON_VALUE(data, '$.time'))     AS ce_time,
  publish_time,
  JSON_QUERY(data, '$.data')                     AS detalle
FROM `${proyecto}.${dataset}.eventos_raw`
WHERE data IS NOT NULL
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY COALESCE(JSON_VALUE(data, '$.id'), message_id)
  ORDER BY publish_time
) = 1
