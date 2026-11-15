"""Utilidades pequeñas compartidas por las tareas de Step Functions."""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.types import TypeDeserializer
from telecorp_comun import nombres

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

SEVERIDADES = ("BAJA", "MEDIA", "ALTA", "CRITICA")
TIPO_ZONA_POR_DEFECTO = "RESIDENCIAL"

# Lima no tiene horario de verano: UTC-5 fijo (evita depender de tzdata en Lambda).
ZONA_LIMA = timezone(timedelta(hours=-5), "America/Lima")

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_des = TypeDeserializer()


def severidad_valida(valor) -> str | None:
    valor = (valor or "").upper() if isinstance(valor, str) else None
    return valor if valor in SEVERIDADES else None


def parsear_fecha(texto: str | None) -> datetime:
    """ISO 8601 (con `Z` o desfase) → datetime UTC. Sin texto: ahora."""
    if not texto:
        return datetime.now(timezone.utc)
    fecha = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)
    return fecha.astimezone(timezone.utc)


def iso(fecha: datetime) -> str:
    """Formato del contrato: `2026-11-15T14:03:00Z`."""
    return fecha.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ulid_determinista(fecha: datetime, semilla: str) -> str:
    """ULID (26 caracteres Crockford) con el tiempo de `fecha` y aleatoriedad
    derivada de `semilla`. El mismo evento produce el mismo id, lo que hace
    idempotentes los reintentos de Step Functions."""
    ms = int(fecha.timestamp() * 1000) & ((1 << 48) - 1)
    azar = int.from_bytes(hashlib.sha256(semilla.encode()).digest()[:10], "big")
    numero = (ms << 80) | azar
    return "".join(_CROCKFORD[(numero >> (5 * i)) & 31] for i in reversed(range(26)))


def leer_zona(zona_id: str, *, cliente=None) -> dict:
    """Ítem del catálogo de zonas (dict vacío si no existe)."""
    cliente = cliente or boto3.client("dynamodb")
    r = cliente.get_item(TableName=nombres.tabla("zonas"), Key={"zonaId": {"S": zona_id}})
    return {k: _des.deserialize(v) for k, v in r.get("Item", {}).items()}


def hijos_de_colectivo(colectivo_id: str, *, cliente=None) -> list[str]:
    """Ids de los reportes agrupados en un colectivo (GSI `por-colectivo`)."""
    cliente = cliente or boto3.client("dynamodb")
    ids: list[str] = []
    paginador = cliente.get_paginator("query")
    for pagina in paginador.paginate(
        TableName=nombres.tabla("incidencias"),
        IndexName="por-colectivo",
        KeyConditionExpression="incidenteColectivoId = :c",
        ExpressionAttributeValues={":c": {"S": colectivo_id}},
        ProjectionExpression="incidenciaId",
    ):
        ids.extend(i["incidenciaId"]["S"] for i in pagina.get("Items", []))
    return ids
