"""Ayudas de transición para las Lambdas auxiliares de Step Functions.

Todo cambio de estado pasa por `telecorp_comun.estados.transicionar()`. Aquí solo
se agrega:

- Camino "hacia adelante": si la incidencia está en REGISTRADA y el destino es
  ATENDIDA, se recorre REGISTRADA → EN_REVISION → ATENDIDA (cada paso válido).
  Nunca se retrocede (ATENDIDA no vuelve a EN_REVISION por un colectivo) ni se
  pasa por CERRADA. Si no hay camino, la transición se ignora.
- Reintento ante conflictos de concurrencia optimista (otra ejecución cambió la
  incidencia entre la lectura y la escritura).
"""

from __future__ import annotations

from collections import deque

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError
from telecorp_comun import nombres
from telecorp_comun.estados import (
    TRANSICIONES,
    Estado,
    IncidenciaNoEncontrada,
    TransicionInvalida,
    _a_decimal,
    ahora_iso,
    obtener,
    transicionar,
)

ORDEN = {
    Estado.REGISTRADA: 0,
    Estado.EN_REVISION: 1,
    Estado.ATENDIDA: 2,
    Estado.SOLUCIONADA: 3,
    Estado.CERRADA: 4,
}

MAX_INTENTOS = 4

_ser = TypeSerializer()


def camino(actual: str, destino: str) -> list[Estado]:
    """Pasos (sin incluir `actual`) para llegar a `destino` solo avanzando.

    Devuelve [] si ya está en el destino, si el destino queda atrás o si no hay
    camino válido.
    """
    origen, meta = Estado(actual), Estado(destino)
    if ORDEN[meta] <= ORDEN[origen]:
        return []
    cola: deque[tuple[Estado, list[Estado]]] = deque([(origen, [])])
    vistos = {origen}
    while cola:
        estado, pasos = cola.popleft()
        for siguiente in sorted(TRANSICIONES[estado], key=ORDEN.get):
            if siguiente in vistos or ORDEN[siguiente] <= ORDEN[estado]:
                continue
            if siguiente == Estado.CERRADA and meta != Estado.CERRADA:
                continue
            nuevos = [*pasos, siguiente]
            if siguiente == meta:
                return nuevos
            vistos.add(siguiente)
            cola.append((siguiente, nuevos))
    return []


def _es_conflicto(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") in (
        "TransactionCanceledException",
        "ConditionalCheckFailedException",
    )


def actualizar_atributos(incidencia_id: str, cambios: dict, *, cliente=None) -> bool:
    """Actualiza atributos que NO son de estado (no genera historial).

    Se usa para guardar la clasificación de la IA o el `incidenteColectivoId`
    cuando la incidencia ya avanzó de estado por otro camino.
    """
    cambios = {
        k: v for k, v in (cambios or {}).items() if k not in ("incidenciaId", "estado", "version") and v is not None
    }
    if not cambios:
        return False
    cliente = cliente or boto3.client("dynamodb")
    cambios["actualizadoEn"] = ahora_iso()
    try:
        cliente.update_item(
            TableName=nombres.tabla("incidencias"),
            Key={"incidenciaId": {"S": incidencia_id}},
            UpdateExpression="SET " + ", ".join(f"#a{i} = :a{i}" for i in range(len(cambios))),
            ConditionExpression="attribute_exists(incidenciaId)",
            ExpressionAttributeNames={f"#a{i}": k for i, k in enumerate(cambios)},
            ExpressionAttributeValues={f":a{i}": _ser.serialize(_a_decimal(v)) for i, v in enumerate(cambios.values())},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
    return True


def mover(
    incidencia_id: str,
    destino: str,
    actor: str,
    comentario: str | None = None,
    cambios: dict | None = None,
    *,
    cliente=None,
) -> dict:
    """Lleva la incidencia hacia `destino` avanzando por transiciones válidas.

    Devuelve `{"incidenciaId", "estadoAnterior", "estadoFinal", "pasos", "transicionado"}`.
    Si la incidencia no existe, devuelve `transicionado=False` y `motivo`.
    Si no hay camino (destino atrás, CERRADA, ya en destino), aplica `cambios`
    sin cambiar el estado (salvo en CERRADA) y no lanza error.
    """
    cliente = cliente or boto3.client("dynamodb")
    cambios = dict(cambios or {})
    pasos_hechos: list[str] = []
    estado_inicial = None
    # Cada paso exitoso y cada conflicto consumen una vuelta.
    for _ in range(MAX_INTENTOS + len(ORDEN)):
        try:
            actual = obtener(incidencia_id, cliente=cliente)
        except IncidenciaNoEncontrada:
            return {"incidenciaId": incidencia_id, "transicionado": False, "motivo": "NO_ENCONTRADA", "pasos": []}
        estado_inicial = estado_inicial or actual["estado"]
        pasos = camino(actual["estado"], destino)
        if not pasos:
            if actual["estado"] != Estado.CERRADA and cambios:
                actualizar_atributos(incidencia_id, cambios, cliente=cliente)
            return {
                "incidenciaId": incidencia_id,
                "estadoAnterior": estado_inicial,
                "estadoFinal": actual["estado"],
                "pasos": pasos_hechos,
                "transicionado": bool(pasos_hechos),
                **({} if pasos_hechos else {"motivo": "SIN_CAMINO"}),
            }
        try:
            # Los cambios de atributos viajan con el primer paso; los siguientes solo cambian estado.
            transicionar(incidencia_id, pasos[0], actor, comentario, cambios, cliente=cliente)
            pasos_hechos.append(str(pasos[0]))
            cambios = {}
        except TransicionInvalida:
            # El estado cambió entre lectura y validación: se recalcula el camino.
            continue
        except ClientError as e:
            if not _es_conflicto(e):
                raise
            continue
    raise RuntimeError(f"No se pudo mover {incidencia_id} a {destino} tras {MAX_INTENTOS} intentos")
