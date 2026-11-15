-- Una fila por cambio de estado de incidencia (Incidencia.CambioEstado).
SELECT
  ce_id,
  JSON_VALUE(detalle, '$.incidenciaId')                     AS incidenciaId,
  JSON_VALUE(detalle, '$.estadoAnterior')                   AS estadoAnterior,
  JSON_VALUE(detalle, '$.estadoNuevo')                      AS estadoNuevo,
  JSON_VALUE(detalle, '$.actor')                            AS actor,
  JSON_VALUE(detalle, '$.comentario')                       AS comentario,
  COALESCE(SAFE.TIMESTAMP(JSON_VALUE(detalle, '$.ts')), ce_time) AS ts,
  JSON_VALUE(detalle, '$.snapshot.zonaId')                  AS zonaId,
  JSON_VALUE(detalle, '$.snapshot.clase')                   AS clase,
  JSON_VALUE(detalle, '$.snapshot.origen')                  AS origen,
  JSON_VALUE(detalle, '$.snapshot.tipo')                    AS tipo,
  SAFE_CAST(JSON_VALUE(detalle, '$.snapshot.prioridad') AS FLOAT64) AS prioridad,
  JSON_VALUE(detalle, '$.snapshot.nivelPrioridad')          AS nivelPrioridad,
  JSON_VALUE(detalle, '$.snapshot.severidadIA')             AS severidadIA,
  JSON_VALUE(detalle, '$.snapshot.incidenteColectivoId')    AS incidenteColectivoId
FROM `${proyecto}.${dataset}.v_eventos`
WHERE ce_type = 'Incidencia.CambioEstado'
