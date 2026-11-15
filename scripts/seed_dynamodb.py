#!/usr/bin/env python3
"""Carga el catálogo de zonas y antenas en DynamoDB (datos semilla de Red TeleCorp).

Uso:
    python scripts/seed_dynamodb.py --stage dev --region us-east-1

Tablas destino (contratos/README.md y contratos/dynamodb.md):
    telecorp-<stage>-zonas     PK zonaId
    telecorp-<stage>-antenas   PK antenaId

Comportamiento:
- Zonas: se escriben con PutItem (sobrescribir es seguro, el catálogo es estático).
- Antenas: solo se insertan las que aún no existen, con estado NORMAL,
  gbPorMinuto 90 y ultimaLectura = ahora. Las antenas ya existentes no se tocan,
  para que volver a ejecutar el seed no reinicie el estado en vivo que mantiene
  evaluar-antena. Por eso el script es idempotente.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import boto3

RAIZ = Path(__file__).resolve().parent.parent
DATOS_POR_DEFECTO = RAIZ / "datos"
STAGE_POR_DEFECTO = "dev"
REGION_POR_DEFECTO = "us-east-1"


def ahora_iso() -> str:
    """Fecha y hora actual en ISO 8601 UTC, p. ej. 2026-11-15T14:03:00Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cargar_json(ruta: Path) -> list[dict]:
    # parse_float=Decimal: DynamoDB no admite float, solo Decimal.
    with ruta.open(encoding="utf-8") as archivo:
        return json.load(archivo, parse_float=Decimal)


def nombre_tabla(stage: str, sufijo: str) -> str:
    return f"telecorp-{stage}-{sufijo}"


def claves_existentes(tabla) -> set[str]:
    """Devuelve los antenaId ya presentes en la tabla (escaneo paginado solo de la clave)."""
    existentes: set[str] = set()
    parametros: dict = {"ProjectionExpression": "antenaId"}
    while True:
        respuesta = tabla.scan(**parametros)
        existentes.update(item["antenaId"] for item in respuesta.get("Items", []))
        if "LastEvaluatedKey" not in respuesta:
            return existentes
        parametros["ExclusiveStartKey"] = respuesta["LastEvaluatedKey"]


def sembrar(dynamodb, stage: str, datos: Path, ahora: str | None = None) -> dict:
    """Carga zonas y antenas. Devuelve un resumen con los conteos realizados."""
    momento = ahora or ahora_iso()
    zonas = cargar_json(datos / "zonas.json")
    antenas = cargar_json(datos / "antenas.json")

    tabla_zonas = dynamodb.Table(nombre_tabla(stage, "zonas"))
    tabla_antenas = dynamodb.Table(nombre_tabla(stage, "antenas"))

    with tabla_zonas.batch_writer() as lote:
        for zona in zonas:
            lote.put_item(Item=zona)

    existentes = claves_existentes(tabla_antenas)
    nuevas = [a for a in antenas if a["antenaId"] not in existentes]
    with tabla_antenas.batch_writer() as lote:
        for antena in nuevas:
            lote.put_item(
                Item={
                    "antenaId": antena["antenaId"],
                    "zonaId": antena["zonaId"],
                    "lat": antena["lat"],
                    "lon": antena["lon"],
                    "estado": "NORMAL",
                    "gbPorMinuto": Decimal("90"),
                    "ultimaLectura": momento,
                    "cambioEstadoEn": momento,
                }
            )

    return {
        "stage": stage,
        "zonasEscritas": len(zonas),
        "antenasEscritas": len(nuevas),
        "antenasOmitidas": len(antenas) - len(nuevas),
    }


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--stage",
        default=os.environ.get("STAGE", STAGE_POR_DEFECTO),
        help="Stage de despliegue (por defecto: $STAGE o dev)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", REGION_POR_DEFECTO),
        help="Región de AWS (por defecto: us-east-1)",
    )
    parser.add_argument(
        "--datos",
        type=Path,
        default=DATOS_POR_DEFECTO,
        help="Carpeta con zonas.json y antenas.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    try:
        resumen = sembrar(dynamodb, args.stage, args.datos)
    except Exception as error:  # noqa: BLE001 - se muestra el error y se sale con código 1
        print(f"Error al sembrar DynamoDB: {error}", file=sys.stderr)
        return 1
    print(
        f"Stage {resumen['stage']}: {resumen['zonasEscritas']} zonas escritas, "
        f"{resumen['antenasEscritas']} antenas nuevas, "
        f"{resumen['antenasOmitidas']} antenas ya existentes (omitidas)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
