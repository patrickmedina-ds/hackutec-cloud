-- Ultimo estado de cada incidencia, con la duracion hasta SOLUCIONADA.
-- duracion_min es NULL mientras la incidencia no se haya solucionado.
SELECT
  incidenciaId,
  estadoNuevo                                            AS estado_actual,
  ts                                                     AS actualizada_en,
  zonaId,
  clase,
  origen,
  tipo,
  prioridad,
  nivelPrioridad,
  severidadIA,
  incidenteColectivoId,
  registrada_en,
  solucionada_en,
  date_diff('minute', registrada_en, solucionada_en)     AS duracion_min
FROM (
  SELECT
    t.*,
    min(ts) OVER (PARTITION BY incidenciaId) AS registrada_en,
    min(CASE WHEN estadoNuevo = 'SOLUCIONADA' THEN ts END) OVER (PARTITION BY incidenciaId) AS solucionada_en,
    row_number() OVER (PARTITION BY incidenciaId ORDER BY ts DESC, ce_id DESC) AS rn
  FROM {{db}}.v_transiciones AS t
  WHERE incidenciaId IS NOT NULL
)
WHERE rn = 1
