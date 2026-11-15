-- Incidentes colectivos (Zona.UmbralSuperado): linea de tiempo por zona.
SELECT
  ce_id,
  JSON_VALUE(detalle, '$.incidenteColectivoId')   AS incidenteColectivoId,
  JSON_VALUE(detalle, '$.zonaId')                 AS zonaId,
  SAFE_CAST(JSON_VALUE(detalle, '$.usuariosDistintos') AS INT64) AS usuariosDistintos,
  SAFE.TIMESTAMP(JSON_VALUE(detalle, '$.ventanaInicio')) AS ventanaInicio,
  SAFE.TIMESTAMP(JSON_VALUE(detalle, '$.ventanaFin'))    AS ventanaFin,
  ARRAY_LENGTH(JSON_VALUE_ARRAY(detalle, '$.incidencias')) AS totalIncidencias,
  COALESCE(SAFE.TIMESTAMP(JSON_VALUE(detalle, '$.ventanaFin')), ce_time) AS detectado_en
FROM `${proyecto}.${dataset}.v_eventos`
WHERE ce_type = 'Zona.UmbralSuperado'
