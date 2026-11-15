-- Ultimo estado de cada incidencia, con la duracion hasta SOLUCIONADA.
-- duracion_min es NULL mientras la incidencia no se haya solucionado.
SELECT
  incidenciaId,
  estadoNuevo                                   AS estado_actual,
  ts                                            AS actualizada_en,
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
  TIMESTAMP_DIFF(solucionada_en, registrada_en, MINUTE) AS duracion_min
FROM (
  SELECT
    t.*,
    MIN(ts) OVER (PARTITION BY incidenciaId) AS registrada_en,
    MIN(IF(estadoNuevo = 'SOLUCIONADA', ts, NULL)) OVER (PARTITION BY incidenciaId) AS solucionada_en
  FROM `${proyecto}.${dataset}.v_transiciones` AS t
)
WHERE incidenciaId IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY incidenciaId ORDER BY ts DESC, ce_id DESC) = 1
