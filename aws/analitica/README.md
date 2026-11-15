# Analítica de Red TeleCorp (AWS)

Reemplaza la analítica que antes estaba en BigQuery y Looker Studio.

```
Bus telecorp-<stage>-eventos --regla--> Firehose --> S3 (eventos/tipo=/anio=/mes=/dia=/, JSON por línea, gzip)
                                                          |
                                     Glue (tabla `eventos`, proyección de particiones) --> Athena (vistas v_*)
                                                          |
                                     Lambda api-analitica --> API Gateway GET /analitica/{reporte}
```

- Entran todos los eventos `telecorp.*` **menos** `Notificacion.Fallida` (lleva teléfono/correo: PII).
- Latencia: ~60 s (buffer de Firehose) más la entrega a S3.
- Recursos: bucket `telecorp-<stage>-analitica-<cuenta>`, base Glue `telecorp_<stage>`,
  workgroup Athena `telecorp-<stage>` (tope de 1 GB escaneado por consulta).
- Sin crawlers: la tabla usa *partition projection* (`tipo`, `anio` 2026-2035, `mes`, `dia`).
  Para años fuera de rango, ajustar `projection.anio.range` en `stacks/analitica.yaml`.

## Cómo se crean las vistas (decisión)

Se usa una **custom resource Lambda** (`crear_vistas.py`) que ejecuta los `CREATE OR REPLACE VIEW`
leyendo `sql/*.sql` en cada despliegue, además de `AWS::Athena::NamedQuery` con el mismo SQL para
ejecutar a mano. Razones: un solo `sam deploy` deja todo listo (sin paso manual que se olvide), el
SQL vive en un único sitio (`sql/`, un test verifica que los NamedQuery coinciden) y se borran limpias
al eliminar el stack. Alternativa descartada: paso en `deploy.sh`, más simple pero dependiente de
quien despliegue. Para reaplicar tras editar un `.sql`, subir `Revision` en el recurso `Vistas`.

## Vistas (`sql/`, Trino/Athena)

| Vista | Contenido |
|---|---|
| `v_eventos` | eventos deduplicados por `id` (`ce_id`, `ce_type`, `ce_time`, `detalle`) |
| `v_transiciones` | un cambio de estado de incidencia por fila |
| `v_incidencias` | último estado por incidencia, `registrada_en`, `solucionada_en`, `duracion_min` |
| `v_antenas_eventos` | cambios de estado de antena, `degradada` |
| `v_colectivos` | incidentes colectivos por zona |
| `v_top_zonas`, `v_top_antenas`, `v_tipos_problema`, `v_recurrencia`, `v_mttr` | agregados (antes en BigQuery) |

Las vistas acumulan todo el histórico; los filtros por `dias` los aplica la API. Como la
deduplicación usa ventanas, Athena no poda particiones por fecha a través de `v_eventos`: el costo
crece con el histórico (el tope de 1 GB por consulta protege). Si crece mucho, consultar `eventos`
con `anio/mes/dia` directamente.

## Cómo consultar

Consola de Athena: workgroup `telecorp-<stage>`, base `telecorp_<stage>`:

```sql
SELECT * FROM v_top_zonas ORDER BY incidencias DESC LIMIT 10;
SELECT * FROM v_mttr ORDER BY mttr_min DESC;
```

CLI:

```bash
aws athena start-query-execution --work-group telecorp-dev \
  --query-execution-context Database=telecorp_dev \
  --query-string "SELECT * FROM v_top_antenas ORDER BY degradaciones DESC LIMIT 10"
```

API (solo grupo `operadores`): `GET /analitica/{reporte}?dias=7` con
`reporte` en `top_zonas | top_antenas | tipos_problema | mttr | colectivos | recurrencia`,
`dias` de 1 a 90 (defecto 7). Respuesta: `{reporte, dias, total, filas[], cache}`. Caché en memoria 5 min.
Errores: 400 (`dias`), 403 (no operador), 404 (reporte), 502/504 (Athena). Ver `INTEGRACION.md`.

## Opcional: QuickSight o Grafana

- **QuickSight**: Datasets -> New -> Athena, workgroup `telecorp-<stage>`, base `telecorp_<stage>`;
  crear un dataset por vista (`v_top_zonas`, `v_mttr`...). Dar a QuickSight acceso al bucket
  `telecorp-<stage>-analitica-<cuenta>` (Manage QuickSight -> Security & permissions) y usar
  SPICE con refresco horario para no escanear en cada vista.
- **Grafana**: plugin *Amazon Athena* (data source con workgroup/base/catálogo `AwsDataCatalog`);
  el rol necesita los permisos de `api-analitica` (`athena:*Query*`, `glue:Get*`, S3 lectura y
  escritura en `athena-resultados/`). Usar `$__timeFilter` sobre `ts`/`detectado_en`.
