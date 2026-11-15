"""Enriquecimiento de los Pipes: imagen de DynamoDB -> `detail` exacto del contrato (contratos/eventos.md).

Una sola función atiende los tres Pipes; distingue la tabla por el ARN del stream
(`eventSourceARN`). Devuelve una lista con un `detail` por registro recibido, en el mismo orden.
"""

from __future__ import annotations

import logging
import re

from boto3.dynamodb.types import TypeDeserializer

log = logging.getLogger()
log.setLevel(logging.INFO)

_des = TypeDeserializer()
_RE_TABLA = re.compile(r":table/([^/]+)/stream/")


def _deserializar(imagen: dict | None) -> dict:
    return {k: _des.deserialize(v) for k, v in (imagen or {}).items()}


def _num(v):
    """Decimal -> int si es entero, si no float (JSON serializable)."""
    if v is None:
        return None
    return int(v) if v == v.to_integral_value() else float(v)


def _limpiar(v):
    """Convierte Decimal anidados (p. ej. prioridad dentro de snapshot)."""
    from decimal import Decimal

    if isinstance(v, Decimal):
        return _num(v)
    if isinstance(v, dict):
        return {k: _limpiar(x) for k, x in v.items()}
    if isinstance(v, (list, set)):
        return [_limpiar(x) for x in v]
    return v


def detalle_cambio_estado(nueva: dict) -> dict:
    snap = _limpiar(nueva.get("snapshot") or {})
    snapshot = {
        "zonaId": snap.get("zonaId"),
        "clase": snap.get("clase"),
        "origen": snap.get("origen"),
        "tipo": snap.get("tipo"),
        "prioridad": snap.get("prioridad"),
        "nivelPrioridad": snap.get("nivelPrioridad"),
        "severidadIA": snap.get("severidadIA"),
        "incidenteColectivoId": snap.get("incidenteColectivoId"),
    }
    return {
        "incidenciaId": nueva["incidenciaId"],
        "estadoAnterior": nueva.get("estadoAnterior"),
        "estadoNuevo": nueva["estadoNuevo"],
        "actor": nueva["actor"],
        "comentario": nueva.get("comentario"),
        "ts": nueva["ts"],
        "snapshot": snapshot,
    }


def detalle_umbral(nueva: dict) -> dict:
    return {
        "zonaId": nueva["zonaId"],
        "incidenteColectivoId": nueva["incidenteColectivoId"],
        "usuariosDistintos": _num(nueva["usuariosDistintos"]),
        "ventanaInicio": nueva["ventanaInicio"],
        "ventanaFin": nueva["ventanaFin"],
        "incidencias": list(nueva.get("incidencias") or []),
    }


def detalle_antena(nueva: dict, anterior: dict) -> dict:
    return {
        "antenaId": nueva["antenaId"],
        "zonaId": nueva["zonaId"],
        "estadoAnterior": anterior.get("estado"),
        "estadoNuevo": nueva["estado"],
        "gbPorMinuto": _num(nueva.get("gbPorMinuto")) or 0,
        "ts": nueva.get("cambioEstadoEn") or nueva.get("ultimaLectura"),
    }


def convertir(registro: dict) -> dict:
    arn = registro.get("eventSourceARN", "")
    m = _RE_TABLA.search(arn)
    tabla = m.group(1) if m else ""
    ddb = registro["dynamodb"]
    nueva = _deserializar(ddb.get("NewImage"))
    if tabla.endswith("-historial-estados"):
        return detalle_cambio_estado(nueva)
    if tabla.endswith("-zona-ventana"):
        return detalle_umbral(nueva)
    if tabla.endswith("-antenas"):
        return detalle_antena(nueva, _deserializar(ddb.get("OldImage")))
    raise ValueError(f"Stream no reconocido: {arn}")


def lambda_handler(event, context):
    registros = event if isinstance(event, list) else [event]
    return [convertir(r) for r in registros]
