"""Máquina de estados de las incidencias.

Toda escritura de estado pasa por aquí: actualiza `incidencias` y agrega una fila
en `historial-estados` en una misma transacción. El stream de `historial-estados`
genera el evento `Incidencia.CambioEstado`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from . import nombres


class Estado(StrEnum):
    REGISTRADA = "REGISTRADA"
    EN_REVISION = "EN_REVISION"
    ATENDIDA = "ATENDIDA"
    SOLUCIONADA = "SOLUCIONADA"
    CERRADA = "CERRADA"


TRANSICIONES: dict[Estado, frozenset[Estado]] = {
    Estado.REGISTRADA: frozenset({Estado.EN_REVISION, Estado.CERRADA}),
    Estado.EN_REVISION: frozenset({Estado.ATENDIDA, Estado.SOLUCIONADA, Estado.CERRADA}),
    Estado.ATENDIDA: frozenset({Estado.EN_REVISION, Estado.SOLUCIONADA}),
    Estado.SOLUCIONADA: frozenset({Estado.EN_REVISION, Estado.CERRADA}),
    Estado.CERRADA: frozenset(),
}

CAMPOS_SNAPSHOT = (
    "zonaId",
    "clase",
    "origen",
    "tipo",
    "prioridad",
    "nivelPrioridad",
    "severidadIA",
    "incidenteColectivoId",
)

_ser = TypeSerializer()
_des = TypeDeserializer()


class TransicionInvalida(Exception):
    pass


class IncidenciaNoEncontrada(Exception):
    pass


def validar_transicion(actual: str, nuevo: str) -> None:
    if Estado(nuevo) not in TRANSICIONES[Estado(actual)]:
        raise TransicionInvalida(f"{actual} → {nuevo} no está permitido")


def ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _a_decimal(valor):
    if isinstance(valor, float):
        return Decimal(str(valor))
    if isinstance(valor, dict):
        return {k: _a_decimal(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_a_decimal(v) for v in valor]
    return valor


def _serializar(item: dict) -> dict:
    return {k: _ser.serialize(_a_decimal(v)) for k, v in item.items() if v is not None}


def _deserializar(item: dict) -> dict:
    return {k: _des.deserialize(v) for k, v in item.items()}


def snapshot(incidencia: dict) -> dict:
    return {c: incidencia.get(c) for c in CAMPOS_SNAPSHOT if incidencia.get(c) is not None}


def _fila_historial(incidencia_id, anterior, nuevo, actor, comentario, ts, incidencia) -> dict:
    return {
        "incidenciaId": incidencia_id,
        "sk": f"{ts}#{nuevo}",
        "estadoAnterior": anterior,
        "estadoNuevo": str(nuevo),
        "actor": actor,
        "comentario": comentario,
        "ts": ts,
        "snapshot": snapshot(incidencia),
    }


def registrar_inicial(incidencia: dict, actor: str = "SISTEMA", *, cliente=None) -> bool:
    """Crea una incidencia en estado REGISTRADA con su primera fila de historial.

    Idempotente: devuelve False si `incidenciaId` ya existía (reintento de SQS).
    """
    cliente = cliente or boto3.client("dynamodb")
    ts = incidencia.get("creadoEn") or ahora_iso()
    item = {
        **incidencia,
        "estado": str(Estado.REGISTRADA),
        "version": 1,
        "creadoEn": ts,
        "actualizadoEn": ts,
    }
    try:
        cliente.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": nombres.tabla("incidencias"),
                        "Item": _serializar(item),
                        "ConditionExpression": "attribute_not_exists(incidenciaId)",
                    }
                },
                {
                    "Put": {
                        "TableName": nombres.tabla("historial-estados"),
                        "Item": _serializar(
                            _fila_historial(item["incidenciaId"], None, Estado.REGISTRADA, actor, None, ts, item)
                        ),
                    }
                },
            ]
        )
    except cliente.exceptions.TransactionCanceledException as e:
        motivos = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
        if motivos and motivos[0] == "ConditionalCheckFailed":
            return False
        raise
    return True


def obtener(incidencia_id: str, *, cliente=None) -> dict:
    cliente = cliente or boto3.client("dynamodb")
    r = cliente.get_item(
        TableName=nombres.tabla("incidencias"),
        Key={"incidenciaId": {"S": incidencia_id}},
        ConsistentRead=True,
    )
    if "Item" not in r:
        raise IncidenciaNoEncontrada(incidencia_id)
    return _deserializar(r["Item"])


def transicionar(
    incidencia_id: str,
    estado_nuevo: str,
    actor: str,
    comentario: str | None = None,
    cambios: dict | None = None,
    *,
    cliente=None,
) -> dict:
    """Cambia el estado validando la transición y con control de concurrencia.

    `cambios` permite actualizar otros atributos en la misma escritura
    (p. ej. prioridad, severidadIA, incidenteColectivoId). Devuelve la incidencia
    actualizada. Lanza TransicionInvalida o IncidenciaNoEncontrada.
    """
    cliente = cliente or boto3.client("dynamodb")
    actual = obtener(incidencia_id, cliente=cliente)
    validar_transicion(actual["estado"], estado_nuevo)

    ts = ahora_iso()
    cambios = {k: v for k, v in (cambios or {}).items() if k not in ("incidenciaId", "estado", "version")}
    nuevos = {**cambios, "estado": str(Estado(estado_nuevo)), "actualizadoEn": ts}
    if Estado(estado_nuevo) == Estado.SOLUCIONADA:
        nuevos["resueltoEn"] = ts

    sets = [f"#a{i} = :a{i}" for i in range(len(nuevos))]
    nombres_attr = {f"#a{i}": k for i, k in enumerate(nuevos)}
    valores = {f":a{i}": _ser.serialize(_a_decimal(v)) for i, v in enumerate(nuevos.values())}
    nombres_attr["#v"] = "version"
    nombres_attr["#e"] = "estado"
    valores[":v"] = {"N": str(actual["version"])}
    valores[":uno"] = {"N": "1"}
    valores[":ea"] = {"S": actual["estado"]}

    resultado = {**actual, **nuevos, "version": int(actual["version"]) + 1}
    cliente.transact_write_items(
        TransactItems=[
            {
                "Update": {
                    "TableName": nombres.tabla("incidencias"),
                    "Key": {"incidenciaId": {"S": incidencia_id}},
                    "UpdateExpression": "SET " + ", ".join(sets) + ", #v = #v + :uno",
                    "ConditionExpression": "#v = :v AND #e = :ea",
                    "ExpressionAttributeNames": nombres_attr,
                    "ExpressionAttributeValues": valores,
                }
            },
            {
                "Put": {
                    "TableName": nombres.tabla("historial-estados"),
                    "Item": _serializar(
                        _fila_historial(incidencia_id, actual["estado"], estado_nuevo, actor, comentario, ts, resultado)
                    ),
                }
            },
        ]
    )
    return resultado
