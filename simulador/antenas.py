"""Simulador de telemetría de antenas (contrato: contratos/integraciones.md, sección 2).

Cada intervalo genera una lectura por antena con la carga útil del contrato:
    {"antenaId": ..., "zonaId": ..., "ts": ..., "gbPorMinuto": ...}

Destinos: kinesis (PutRecords), iot (publicación MQTT) o stdout.
Ejemplo: python simulador/antenas.py --escenario caida --zona LIM-MIR-01 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

from comun import (
    REGION_POR_DEFECTO,
    UMBRAL_CAIDA_GB,
    UMBRAL_SATURACION_GB,
    ErrorSimulador,
    ahora_utc,
    antenas_de_zona,
    cargar_antenas,
    cargar_zonas,
    crear_cliente,
    exigir_antena,
    exigir_zona,
    iso_z,
    nombre_recurso,
    ruta_datos,
    stage_por_defecto,
)

ESCENARIOS = ("normal", "caida", "saturacion", "inhabilitada", "recurrente")
DESTINOS = ("kinesis", "iot", "stdout")

# Rangos de valores generados. Deben caer dentro de los umbrales de telecorp_comun.
RANGO_NORMAL = (UMBRAL_CAIDA_GB, UMBRAL_SATURACION_GB)  # 60 a 129 GB/min, ambos incluidos
RANGO_CAIDA = (5.0, 59.0)  # estrictamente menor que 60 y mayor que 0
RANGO_SATURACION = (130.0, 180.0)  # estrictamente mayor que 129

# Recurrente: 6 fases alternas (caída, recuperación, caída, ...) = 3 caídas y 3 recuperaciones.
FASES_RECURRENTE = 6

KINESIS_MAX_REGISTROS = 500  # límite de PutRecords


def _redondear(valor: float) -> float:
    return round(valor, 1)


def generar_gb(escenario: str, afectada: bool, paso: int, total_pasos: int, rng: random.Random) -> float:
    """Devuelve el GB/min de una antena en un paso del escenario.

    Solo las antenas afectadas se desvían del funcionamiento normal.
    """
    if escenario == "normal" or not afectada:
        return _redondear(min(max(rng.uniform(*RANGO_NORMAL), RANGO_NORMAL[0]), RANGO_NORMAL[1]))
    if escenario == "caida":
        return _redondear(rng.uniform(*RANGO_CAIDA))
    if escenario == "saturacion":
        return _redondear(rng.uniform(*RANGO_SATURACION))
    if escenario == "inhabilitada":
        return 0.0
    if escenario == "recurrente":
        fase = (paso * FASES_RECURRENTE) // max(total_pasos, 1)
        fase = min(fase, FASES_RECURRENTE - 1)
        if fase % 2 == 0:  # fases pares: caída
            return _redondear(rng.uniform(*RANGO_CAIDA))
        return _redondear(min(max(rng.uniform(*RANGO_NORMAL), RANGO_NORMAL[0]), RANGO_NORMAL[1]))
    raise ErrorSimulador(f"escenario desconocido: {escenario}")


def calcular_afectadas(escenario: str, antenas: list[dict], zona_id: str | None, antena_id: str | None) -> set[str]:
    """IDs de las antenas que el escenario afecta."""
    if escenario in ("caida", "saturacion"):
        return {a["antenaId"] for a in antenas_de_zona(antenas, zona_id)}
    if escenario in ("inhabilitada", "recurrente"):
        return {antena_id}
    return set()


def generar_lecturas(
    antenas: list[dict],
    escenario: str,
    afectadas: set[str],
    paso: int,
    total_pasos: int,
    rng: random.Random,
    instante,
) -> list[dict]:
    """Una lectura por antena con la carga útil exacta de integraciones.md sección 2."""
    lecturas = []
    for antena in antenas:
        gb = generar_gb(escenario, antena["antenaId"] in afectadas, paso, total_pasos, rng)
        lecturas.append(
            {
                "antenaId": antena["antenaId"],
                "zonaId": antena["zonaId"],
                "ts": iso_z(instante),
                "gbPorMinuto": gb,
            }
        )
    return lecturas


def topico_iot(antena_id: str) -> str:
    return f"telecorp/antenas/{antena_id}/trafico"


def comando_iot_equivalente(topico: str, carga: str) -> str:
    """Comando de la CLI equivalente a la publicación, para mostrarlo en dry-run."""
    return (
        f"aws iot-data publish --topic '{topico}' "
        f"--cli-binary-format raw-in-base64-out --payload '{carga}'"
    )


def publicar_kinesis(lecturas: list[dict], nombre_stream: str, cliente) -> int:
    """Envía las lecturas con PutRecords. La clave de partición es el antenaId."""
    registros = [
        {"Data": json.dumps(lectura, ensure_ascii=False).encode("utf-8"), "PartitionKey": lectura["antenaId"]}
        for lectura in lecturas
    ]
    fallidos = 0
    for inicio in range(0, len(registros), KINESIS_MAX_REGISTROS):
        respuesta = cliente.put_records(
            StreamName=nombre_stream, Records=registros[inicio : inicio + KINESIS_MAX_REGISTROS]
        )
        fallidos += respuesta.get("FailedRecordCount", 0)
    if fallidos:
        raise ErrorSimulador(f"Kinesis rechazó {fallidos} de {len(registros)} registros")
    return len(registros)


def publicar_iot(lecturas: list[dict], cliente) -> int:
    """Publica cada lectura en `telecorp/antenas/{antenaId}/trafico` (QoS 1)."""
    for lectura in lecturas:
        carga = json.dumps(lectura, ensure_ascii=False)
        cliente.publish(topic=topico_iot(lectura["antenaId"]), qos=1, payload=carga.encode("utf-8"))
    return len(lecturas)


def publicar_lecturas(
    lecturas: list[dict],
    destino: str,
    *,
    stage: str,
    region: str,
    iot_endpoint: str | None = None,
    dry_run: bool = False,
    cliente=None,
) -> int:
    """Envía un lote de lecturas al destino elegido. En dry-run no crea clientes ni llama a la nube."""
    if destino == "stdout":
        for lectura in lecturas:
            print(json.dumps(lectura, ensure_ascii=False))
        return len(lecturas)

    if destino == "kinesis":
        nombre_stream = nombre_recurso(stage, "telemetria")
        if dry_run:
            print(f"[dry-run] PutRecords a {nombre_stream}: {len(lecturas)} registros", file=sys.stderr)
            for lectura in lecturas:
                print(f"[dry-run]   PartitionKey={lectura['antenaId']} Data={json.dumps(lectura)}", file=sys.stderr)
            return len(lecturas)
        cliente = cliente or crear_cliente("kinesis", region)
        return publicar_kinesis(lecturas, nombre_stream, cliente)

    if destino == "iot":
        if dry_run:
            for lectura in lecturas:
                carga = json.dumps(lectura, ensure_ascii=False)
                print(f"[dry-run] {comando_iot_equivalente(topico_iot(lectura['antenaId']), carga)}", file=sys.stderr)
            return len(lecturas)
        cliente = cliente or crear_cliente("iot-data", region, iot_endpoint)
        return publicar_iot(lecturas, cliente)

    raise ErrorSimulador(f"destino desconocido: {destino}")


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Simulador de telemetría de antenas de Red TeleCorp.",
        epilog=(
            "Escenarios: normal (60-129 GB/min), caida --zona X (<60), saturacion --zona X (>129), "
            "inhabilitada --antena Y (0), recurrente --antena Y (3 caídas y 3 recuperaciones)."
        ),
    )
    parser.add_argument("--escenario", choices=ESCENARIOS, default="normal", help="Escenario a simular.")
    parser.add_argument("--zona", help="zonaId afectada (obligatorio en caida y saturacion).")
    parser.add_argument("--antena", help="antenaId afectada (obligatorio en inhabilitada y recurrente).")
    parser.add_argument("--destino", choices=DESTINOS, default="stdout", help="Destino de las lecturas.")
    parser.add_argument("--duracion", type=int, default=60, help="Duración total en segundos.")
    parser.add_argument("--intervalo", type=int, default=10, help="Segundos entre lecturas.")
    parser.add_argument("--stage", default=stage_por_defecto(), help="Stage de despliegue (por defecto STAGE o dev).")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", REGION_POR_DEFECTO), help="Región AWS.")
    parser.add_argument("--iot-endpoint", help="Endpoint de IoT Data (solo destino iot, opcional).")
    parser.add_argument("--datos", help="Carpeta con zonas.json y antenas.json (por defecto datos/ o datos_ejemplo/).")
    parser.add_argument("--semilla", type=int, help="Semilla del generador aleatorio, para repetir resultados.")
    parser.add_argument("--dry-run", action="store_true", help="Muestra lo que enviaría sin llamar a la nube.")
    return parser


def _ejecutar(args: argparse.Namespace) -> int:
    if args.intervalo <= 0 or args.duracion <= 0:
        raise ErrorSimulador("--duracion e --intervalo deben ser mayores que cero")
    total = max(1, args.duracion // args.intervalo)

    datos = ruta_datos(args.datos)
    zonas = cargar_zonas(datos)
    antenas = cargar_antenas(datos)

    zona_id = antena_id = None
    if args.escenario in ("caida", "saturacion"):
        if not args.zona:
            raise ErrorSimulador(f"el escenario {args.escenario} requiere --zona")
        zona_id = exigir_zona(zonas, args.zona)["zonaId"]
        if not antenas_de_zona(antenas, zona_id):
            raise ErrorSimulador(f"la zona {zona_id} no tiene antenas en los datos")
    if args.escenario in ("inhabilitada", "recurrente"):
        if not args.antena:
            raise ErrorSimulador(f"el escenario {args.escenario} requiere --antena")
        antena_id = exigir_antena(antenas, args.antena)["antenaId"]
    if args.escenario == "recurrente" and total < FASES_RECURRENTE:
        raise ErrorSimulador(
            f"recurrente necesita al menos {FASES_RECURRENTE} lecturas; "
            f"con duración {args.duracion} s e intervalo {args.intervalo} s hay {total}"
        )

    afectadas = calcular_afectadas(args.escenario, antenas, zona_id, antena_id)
    rng = random.Random(args.semilla)
    modo = " (dry-run)" if args.dry_run else ""
    print(
        f"Escenario {args.escenario} · {len(antenas)} antenas · {total} lecturas · "
        f"destino {args.destino}{modo}",
        file=sys.stderr,
    )

    for paso in range(total):
        lecturas = generar_lecturas(antenas, args.escenario, afectadas, paso, total, rng, ahora_utc())
        enviadas = publicar_lecturas(
            lecturas,
            args.destino,
            stage=args.stage,
            region=args.region,
            iot_endpoint=args.iot_endpoint,
            dry_run=args.dry_run,
        )
        print(f"Paso {paso + 1}/{total}: {enviadas} lecturas", file=sys.stderr)
        if not args.dry_run and paso < total - 1:
            time.sleep(args.intervalo)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    try:
        return _ejecutar(args)
    except ErrorSimulador as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001  (CLI: muestra el fallo de la nube sin traceback)
        print(f"error de ejecución: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
