"""Evalúa la telemetría de antenas (Kinesis) y registra métricas y cambios de estado.

- Cada lectura se guarda en `metricas-antena` con TTL de 30 días.
- `antenas` se actualiza SOLO cuando cambia el estado (`estado <> :nuevo`), de modo que cada
  MODIFY del stream de `antenas` es un cambio real (el Pipe lo convierte en `Antena.CambioEstado`).
- Los registros ilegibles se descartan (no deben bloquear el shard); los fallos de DynamoDB se
  reportan con `ReportBatchItemFailures` para que Kinesis reintente / divida el lote.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3

from telecorp_comun import nombres
from telecorp_comun.antenas import clasificar_trafico

log = logging.getLogger()
log.setLevel(logging.INFO)

TTL_METRICAS_DIAS = 30
_cliente = None


def cliente():
    global _cliente
    if _cliente is None:
        _cliente = boto3.client("dynamodb")
    return _cliente


def _epoch(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


def leer_lectura(registro: dict) -> dict | None:
    """Decodifica y valida una lectura; None si es inválida."""
    try:
        d = json.loads(base64.b64decode(registro["kinesis"]["data"]))
        antena_id, zona_id, ts, gb = d["antenaId"], d["zonaId"], d["ts"], d["gbPorMinuto"]
        if isinstance(gb, bool) or not isinstance(gb, (int, float)):
            raise ValueError("gbPorMinuto no numérico")
        _epoch(ts)
        return {"antenaId": str(antena_id), "zonaId": str(zona_id), "ts": str(ts), "gb": gb, "estado": str(clasificar_trafico(gb))}
    except (KeyError, ValueError, TypeError) as e:
        log.warning("Lectura descartada: %s", e)
        return None


def escribir_metrica(lectura: dict) -> None:
    cliente().put_item(
        TableName=nombres.tabla("metricas-antena"),
        Item={
            "antenaId": {"S": lectura["antenaId"]},
            "ts": {"S": lectura["ts"]},
            "gbPorMinuto": {"N": str(Decimal(str(lectura["gb"])))},
            "estado": {"S": lectura["estado"]},
            "zonaId": {"S": lectura["zonaId"]},
            "ttl": {"N": str(_epoch(lectura["ts"]) + int(timedelta(days=TTL_METRICAS_DIAS).total_seconds()))},
        },
    )


def estado_actual(antena_id: str) -> str | None:
    r = cliente().get_item(
        TableName=nombres.tabla("antenas"),
        Key={"antenaId": {"S": antena_id}},
        ProjectionExpression="estado",
        ConsistentRead=True,
    )
    return r["Item"]["estado"]["S"] if "Item" in r else None


def sembrar_antena(lectura: dict) -> None:
    """Antena desconocida: se crea en NORMAL para que el primer cambio sea un MODIFY."""
    try:
        cliente().put_item(
            TableName=nombres.tabla("antenas"),
            Item={
                "antenaId": {"S": lectura["antenaId"]},
                "zonaId": {"S": lectura["zonaId"]},
                "estado": {"S": "NORMAL"},
                "gbPorMinuto": {"N": str(Decimal(str(lectura["gb"])))},
                "ultimaLectura": {"S": lectura["ts"]},
                "cambioEstadoEn": {"S": lectura["ts"]},
            },
            ConditionExpression="attribute_not_exists(antenaId)",
        )
    except cliente().exceptions.ConditionalCheckFailedException:
        pass


def actualizar_estado(lectura: dict) -> bool:
    """Actualiza `antenas` solo si el estado difiere. Devuelve True si hubo cambio."""
    try:
        cliente().update_item(
            TableName=nombres.tabla("antenas"),
            Key={"antenaId": {"S": lectura["antenaId"]}},
            UpdateExpression="SET #e = :n, gbPorMinuto = :g, ultimaLectura = :t, cambioEstadoEn = :t, zonaId = :z",
            ConditionExpression="#e <> :n",
            ExpressionAttributeNames={"#e": "estado"},
            ExpressionAttributeValues={
                ":n": {"S": lectura["estado"]},
                ":g": {"N": str(Decimal(str(lectura["gb"])))},
                ":t": {"S": lectura["ts"]},
                ":z": {"S": lectura["zonaId"]},
            },
        )
        return True
    except cliente().exceptions.ConditionalCheckFailedException:
        return False


def lambda_handler(event, context):
    conocido: dict[str, str] = {}  # estado de cada antena dentro del lote, evita lecturas repetidas
    for registro in event.get("Records", []):
        lectura = leer_lectura(registro)
        if lectura is None:
            continue
        try:
            escribir_metrica(lectura)
            antena = lectura["antenaId"]
            if antena not in conocido:
                actual = estado_actual(antena)
                if actual is None:
                    sembrar_antena(lectura)
                    actual = "NORMAL"
                conocido[antena] = actual
            if conocido[antena] != lectura["estado"]:
                if actualizar_estado(lectura):
                    log.info("Antena %s: %s -> %s", antena, conocido[antena], lectura["estado"])
                conocido[antena] = lectura["estado"]
        except Exception:  # noqa: BLE001
            log.exception("Fallo evaluando %s", lectura["antenaId"])
            return {"batchItemFailures": [{"itemIdentifier": registro["kinesis"]["sequenceNumber"]}]}
    return {"batchItemFailures": []}
