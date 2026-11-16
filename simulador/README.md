# Simulador de Red TeleCorp

Genera telemetría de antenas y reportes de usuarios para probar la plataforma y preparar el pitch.
Cumple los contratos de `contratos/integraciones.md` (secciones 1 y 2) y `contratos/openapi.yaml` (`ReportePublico`).
Solo reportan **clientes con contrato ACTIVO** de `clientes.json`: zona, DNI, número de servicio y contacto salen del contrato, y `usuarioId = cliente:<numeroServicio>`.

## Archivos

| Archivo | Qué hace |
|---|---|
| `antenas.py` | Telemetría de antenas: escenarios `normal`, `caida`, `saturacion`, `inhabilitada`, `recurrente`. |
| `reportes.py` | Reportes de usuarios: escenarios `aislado`, `masivo`, `tormenta`. |
| `demo.py` | Guion del pitch: normal, caída de una zona y 12 reportes en esa zona. |
| `comun.py` | Datos de entrada, nombres de recursos y reloj. Usa `telecorp_comun.antenas` para los umbrales. |
| `datos_ejemplo/` | 3 zonas, 6 antenas y 54 clientes de ejemplo, usadas si no existe `datos/` en la raíz del repo. |
| `tests/` | Pruebas pytest (moto para SQS y Kinesis). |

## Requisitos

Python 3.11 o superior (el venv del proyecto ya tiene `boto3` y `moto`):

```bash
PY=.venv/bin/python
```

Para enviar a la nube hacen falta credenciales de AWS. Con `--dry-run` no se llama a ningún servicio.

## Datos de entrada

El simulador busca `zonas.json`, `antenas.json` y `clientes.json` en este orden:

1. La carpeta indicada con `--datos`.
2. `datos/` en la raíz del repositorio, si existe.
3. `simulador/datos_ejemplo/`.

## Antenas

```bash
# Lecturas normales en consola, cada 10 s durante 1 minuto
$PY simulador/antenas.py --escenario normal --duracion 60 --intervalo 10

# Caída de una zona, a Kinesis (telecorp-dev-telemetria)
$PY simulador/antenas.py --escenario caida --zona LIM-SJL-01 --destino kinesis

# Saturación de una zona, por IoT (telecorp/antenas/{antenaId}/trafico)
$PY simulador/antenas.py --escenario saturacion --zona LIM-MIR-01 --destino iot

# Una antena inhabilitada (0 GB/min)
$PY simulador/antenas.py --escenario inhabilitada --antena ANT-LIM-SBO-03-01 --dry-run

# Antena recurrente: 3 caídas y 3 recuperaciones en el periodo (mínimo 6 lecturas)
$PY simulador/antenas.py --escenario recurrente --antena ANT-LIM-MIR-01-01 --duracion 600 --intervalo 60 --destino kinesis
```

Opciones principales: `--destino {kinesis,iot,stdout}`, `--duracion` y `--intervalo` (segundos),
`--stage` (por defecto `$STAGE` o `dev`), `--region` (por defecto `us-east-1`),
`--semilla` (para repetir la misma secuencia), `--dry-run`.

Umbrales (de `telecorp_comun.antenas`): menos de 60 GB/min es caída, más de 129 es saturación, 0 es inhabilitada.
`normal` genera valores entre 60 y 129.

## Reportes

```bash
# Un reporte por usuario en zonas y horas distintas (5 por defecto)
$PY simulador/reportes.py --escenario aislado --reportes 5 --destino sqs

# 15 clientes distintos de la misma zona, uno por cliente, en 10 minutos (umbral colectivo: 10)
# Error claro si la zona no tiene tantos clientes ACTIVO (en datos/ hay 16 por zona).
$PY simulador/reportes.py --escenario masivo --zona LIM-SJL-01 --usuarios 15 --minutos 10 --tipo SIN_INTERNET --destino sqs

# Ráfaga de 2000 reportes en varias zonas, enviada en lotes de 10
$PY simulador/reportes.py --escenario tormenta --reportes 2000 --destino sqs --dry-run

# Enviar a la API pública (POST /reportes, sin token, espera 202)
$PY simulador/reportes.py --escenario masivo --zona LIM-JMA-01 --usuarios 12 --destino api \
  --api-url https://<apiId>.execute-api.us-east-1.amazonaws.com/dev
```

Destinos:

- `sqs`: `SendMessageBatch` a `telecorp-<stage>-reportes`, de 10 en 10, con el cuerpo del contrato
  (`incidenciaId` uuid4, `usuarioId = cliente:<numeroServicio>`, `recibidoEn` en epoch ms, `reporte`).
- `api`: un `POST /reportes` por reporte con `{documento, numeroServicio, tipo, descripcion, fechaHoraEvento}`;
  la Lambda valida el contrato. Los clientes deben estar sembrados (`scripts/seed_dynamodb.py`).
- `stdout`: imprime el cuerpo SQS de cada reporte, una línea por reporte.

Los clientes vienen de `clientes.json` (DNI de 8 dígitos, `SRV-` + 8 dígitos, celular `+519XXXXXXXX`,
correo `@example.com`); el simulador solo inventa la hora, el tipo y la descripción.

## Demo del pitch

```bash
# Todo en consola, sin llamar a la nube
$PY simulador/demo.py --dry-run

# Telemetría a stdout y reportes a SQS (requiere credenciales)
$PY simulador/demo.py --destino sqs --stage dev --zona LIM-SJL-01

# Reportes por la API pública (POST /reportes, sin token)
$PY simulador/demo.py --destino api --api-url https://<apiId>.execute-api.us-east-1.amazonaws.com/dev --zona LIM-SJL-01
```

La demo imprime cada paso con la tabla de antenas (estado según el umbral) y la lista
de lo que debería ocurrir en la plataforma. Los pasos son: normal, caída de la zona y
12 reportes en esa zona, que superan el umbral de 10 usuarios distintos.

## Pruebas

```bash
cd .
$PY -m pytest simulador/tests -q
```

Las pruebas usan moto y credenciales falsas. Comprueban los rangos por escenario, la carga útil de
antenas y reportes, los usuarios distintos del escenario `masivo` y los envíos a Kinesis y SQS.

## Notas

- `--dry-run` no crea clientes de AWS y no sigue el ritmo de `--intervalo`.
- Para `--destino iot` en una cuenta real puede hacer falta `--iot-endpoint` (el endpoint ATS de IoT Data).
- `recibidoEn` se envía en milisegundos (equivalente a `$context.requestTimeEpoch`).
