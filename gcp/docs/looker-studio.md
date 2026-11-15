# Tablero del NOC en Looker Studio

Looker Studio es gratuito; el único costo es el de las consultas de BigQuery que dispara. Con los volúmenes del proyecto es prácticamente cero.

## 1. Conectar las vistas

1. Entra a <https://lookerstudio.google.com> con una cuenta que tenga `roles/bigquery.dataViewer` sobre el dataset `telecorp_analitica` y `roles/bigquery.jobUser` en el proyecto.
2. **Crear > Informe > Agregar datos > BigQuery**. Elige proyecto, dataset `telecorp_analitica` y una vista. Repite una vez por vista (cada vista es una fuente de datos).
3. En **Recurso > Administrar fuentes de datos** pon la credencial en **Credenciales del propietario**, para que los espectadores no necesiten permisos en BigQuery.
4. Bajo costo: en cada fuente deja la **actualización de datos en 12 horas** o 1 hora, no en 15 minutos, y activa la **caché**.

| Fuente de datos | Vista |
|---|---|
| Incidencias | `v_incidencias` |
| Zonas | `v_top_zonas` |
| Antenas | `v_top_antenas`, `v_antenas_eventos` |
| Tipos | `v_tipos_problema` |
| MTTR | `v_mttr` |
| Colectivos | `v_colectivos`, `v_recurrencia` |
| Pronóstico | consulta personalizada (ver gráfico 6) |

## 2. Gráficos recomendados

1. **Mapa por zona** (`v_top_zonas`): gráfico de mapa de burbujas o coropleta. Dimensión: `zonaId`. Métrica: `incidencias` (tamaño/color); `abiertas` en la información emergente. Looker Studio no reconoce códigos como `LIM-MIR-01`: crea en la fuente un campo calculado `Ubicación` (por ejemplo, `CASE zonaId WHEN 'LIM-MIR-01' THEN '-12.12,-77.03' ... END`, tipo *Geografía > Latitud, Longitud*) o une una tabla de zonas con lat/lon (la tabla `zonas` de DynamoDB exportada a CSV o a una hoja de cálculo).
2. **Top de antenas** (`v_top_antenas`): barras horizontales, dimensión `antenaId`, métricas `caidas`, `saturaciones`, `inhabilitaciones` apiladas, orden `degradaciones` descendente, 10 filas.
3. **Tipos de problema** (`v_tipos_problema`): gráfico de anillo con dimensión `tipo` y métrica `incidencias`; filtro de fecha con `dia`. Opcional: serie de tiempo apilada por `tipo`.
4. **MTTR** (`v_mttr`): tabla con mapa de calor o barras agrupadas: dimensión `zonaId`, dimensión de desglose `nivelPrioridad`, métrica `mttr_min` (promedio). Agrega un indicador (scorecard) con el promedio global ponderado por `resueltas`.
5. **Línea de tiempo de colectivos** (`v_colectivos`): serie de tiempo con dimensión `detectado_en` (granularidad hora) y métrica `COUNT(incidenteColectivoId)`, desglose por `zonaId`; al lado, tabla de `v_recurrencia` filtrada con `es_recurrente = true`.
6. **Pronóstico** (modelo BigQuery ML): fuente por **consulta personalizada** con la segunda sentencia de `gcp/sql/modelo_pronostico.sql` (reemplaza proyecto y dataset). Gráfico de serie de tiempo con `dia`, métrica `degradaciones_previstas` y bandas `limite_inferior`/`limite_superior` (puedes mostrarlos como líneas finas). Control de lista desplegable por `zonaId`. Para que cueste menos, guarda el resultado en una tabla con una segunda consulta programada y conéctala en lugar de la consulta personalizada.

Fila superior sugerida: indicadores de incidencias abiertas (`v_incidencias`, filtro `estado_actual` distinto de SOLUCIONADA/CERRADA), colectivos activos y antenas caídas.

## 3. Buenas prácticas

- Controles de filtro globales: rango de fechas, `zonaId`, `nivelPrioridad`.
- Las vistas no garantizan orden; ordena en cada gráfico.
- `v_incidencias` y `v_top_*` recalculan sobre `eventos_raw` en cada consulta; es barato con miles de eventos. Si el volumen crece, materializa las vistas con una consulta programada.
- Los eventos `Notificacion.Fallida` incluyen PII (`destino`); las vistas de este proyecto **no** exponen esa columna.
