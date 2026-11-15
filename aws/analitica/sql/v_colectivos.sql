-- Incidentes colectivos (Zona.UmbralSuperado): linea de tiempo por zona.
SELECT
  ce_id,
  detalle.incidenteColectivoId                           AS incidenteColectivoId,
  detalle.zonaId                                         AS zonaId,
  detalle.usuariosDistintos                              AS usuariosDistintos,
  TRY(CAST(from_iso8601_timestamp(detalle.ventanaInicio) AS timestamp)) AS ventanaInicio,
  TRY(CAST(from_iso8601_timestamp(detalle.ventanaFin) AS timestamp))    AS ventanaFin,
  cardinality(detalle.incidencias)                       AS totalIncidencias,
  COALESCE(TRY(CAST(from_iso8601_timestamp(detalle.ventanaFin) AS timestamp)), ce_time) AS detectado_en
FROM {{db}}.v_eventos
WHERE ce_type = 'Zona.UmbralSuperado'
