# Pronóstico con BigQuery ML

- Modelo: `telecorp_analitica.modelo_degradaciones` (`ARIMA_PLUS`, una serie por `zonaId`, frecuencia diaria, horizonte 7 días). SQL en `gcp/sql/modelo_pronostico.sql`.
- Reentrenamiento: Terraform crea una consulta programada (`google_bigquery_data_transfer_config.reentrenar_pronostico`, `every monday 06:00` UTC) que ejecuta la primera sentencia con una cuenta de servicio propia. Se apaga con `habilitar_reentrenamiento = false`.
- Se necesitan al menos unos días de datos de `Antena.CambioEstado` por zona; sin datos suficientes el entrenamiento falla sin costo y se reintenta la semana siguiente. Para la demo, carga eventos de varios días (el simulador) y entrena a mano.

## Entrenar a mano

```bash
sed -e 's/${proyecto}/MI_PROYECTO/g' -e 's/${dataset}/telecorp_analitica/g' gcp/sql/modelo_pronostico.sql > /tmp/modelo.sql
bq query --use_legacy_sql=false --location=US < /tmp/modelo.sql   # ejecuta ambas sentencias
```

## Si la consulta programada no se puede crear con Terraform

(por ejemplo, falta de permisos para suplantar la cuenta de servicio) créala en la consola: **BigQuery > Consultas programadas > Crear**, pega la sentencia `CREATE OR REPLACE MODEL`, repetición *Semanal, lunes 06:00*, ubicación `US`. O con CLI:

```bash
bq mk --transfer_config --data_source=scheduled_query --location=US \
  --display_name="reentrenar-pronostico" --schedule="every monday 06:00" \
  --target_dataset=telecorp_analitica \
  --params='{"query":"CREATE OR REPLACE MODEL ..."}'
```
