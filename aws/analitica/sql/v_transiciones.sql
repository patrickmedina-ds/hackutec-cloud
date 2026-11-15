-- Una fila por cambio de estado de incidencia (Incidencia.CambioEstado).
SELECT
  ce_id,
  detalle.incidenciaId                                   AS incidenciaId,
  detalle.estadoAnterior                                 AS estadoAnterior,
  detalle.estadoNuevo                                    AS estadoNuevo,
  detalle.actor                                          AS actor,
  detalle.comentario                                     AS comentario,
  COALESCE(TRY(CAST(from_iso8601_timestamp(detalle.ts) AS timestamp)), ce_time) AS ts,
  detalle.snapshot.zonaId                                AS zonaId,
  detalle.snapshot.clase                                 AS clase,
  detalle.snapshot.origen                                AS origen,
  detalle.snapshot.tipo                                  AS tipo,
  detalle.snapshot.prioridad                             AS prioridad,
  detalle.snapshot.nivelPrioridad                        AS nivelPrioridad,
  detalle.snapshot.severidadIA                           AS severidadIA,
  detalle.snapshot.incidenteColectivoId                  AS incidenteColectivoId
FROM {{db}}.v_eventos
WHERE ce_type = 'Incidencia.CambioEstado'
