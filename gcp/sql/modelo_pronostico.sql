-- Pronostico de degradaciones diarias por zona con BigQuery ML (ARIMA_PLUS).
-- El archivo tiene DOS sentencias separadas por la marca de pronostico:
--   1) entrenamiento (la programa Terraform cada semana; tambien se puede ejecutar a mano)
--   2) consulta del pronostico (para Looker Studio o la consola)
-- Terraform sustituye proyecto y dataset; para ejecutar a mano reemplaza esos dos marcadores.

CREATE OR REPLACE MODEL `${proyecto}.${dataset}.modelo_degradaciones`
OPTIONS (
  model_type = 'ARIMA_PLUS',
  time_series_timestamp_col = 'dia',
  time_series_data_col = 'degradaciones',
  time_series_id_col = 'zonaId',
  auto_arima = TRUE,
  data_frequency = 'DAILY',
  horizon = 7
) AS
SELECT
  DATE(ts)             AS dia,
  zonaId,
  COUNT(*)             AS degradaciones
FROM `${proyecto}.${dataset}.v_antenas_eventos`
WHERE degradada
  AND zonaId IS NOT NULL
GROUP BY dia, zonaId;

-- @@ PRONOSTICO
-- Proximos 7 dias por zona, con intervalo de confianza del 90 %.
SELECT
  zonaId,
  DATE(forecast_timestamp)                  AS dia,
  ROUND(forecast_value, 2)                  AS degradaciones_previstas,
  ROUND(prediction_interval_lower_bound, 2) AS limite_inferior,
  ROUND(prediction_interval_upper_bound, 2) AS limite_superior
FROM ML.FORECAST(
  MODEL `${proyecto}.${dataset}.modelo_degradaciones`,
  STRUCT(7 AS horizon, 0.9 AS confidence_level)
)
ORDER BY zonaId, dia;
