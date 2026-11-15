"""Consumidor SQS de reportes: registra la incidencia y mantiene la ventana de 24 h por zona.

Flujo por mensaje (contratos/integraciones.md §1 y dynamodb.md `zona-ventana`):
1. Valida el reporte y arma la incidencia INDIVIDUAL / USUARIO.
2. `registrar_inicial()` (idempotente).
3. Inserta el ítem `R#<ts>#<usuarioId>` en `zona-ventana`.
4. Si ya existe el marcador `COLECTIVO_ACTIVO`, agrupa la incidencia nueva en ese colectivo.
   Si no, cuenta usuarios distintos de las últimas 24 h y al llegar a 10 crea el marcador
   (condicional, así solo se dispara una vez; el Pipe lo convierte en `Zona.UmbralSuperado`).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from telecorp_comun import eventos, nombres
from telecorp_comun.estados import (
    TRANSICIONES,
    Estado,
    IncidenciaNoEncontrada,
    TransicionInvalida,
    obtener,
    registrar_inicial,
    transicionar,
)

log = logging.getLogger()
log.setLevel(logging.INFO)

MARCADOR = "COLECTIVO_ACTIVO"
TIPOS = {"SIN_INTERNET", "SIN_LLAMADAS", "SIN_SMS", "LENTITUD", "INTERMITENCIA", "OTRO"}
OBLIGATORIOS = ("documento", "numeroServicio", "zonaId", "fechaHoraEvento", "tipo", "descripcion")
OPCIONALES = ("telefono", "email", "lat", "lon")

_ser = TypeSerializer()
_des = TypeDeserializer()
_cliente = None


class ReporteInvalido(ValueError):
    pass


def cliente():
    global _cliente
    if _cliente is None:
        _cliente = boto3.client("dynamodb")
    return _cliente


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _dec(v):
    if isinstance(v, float):
        return Decimal(str(v))
    if isinstance(v, list):
        return [_dec(x) for x in v]
    return v


def _item(d: dict) -> dict:
    return {k: _ser.serialize(_dec(v)) for k, v in d.items() if v is not None}


def validar(cuerpo: dict) -> dict:
    """Devuelve la incidencia lista para `registrar_inicial()` o lanza ReporteInvalido."""
    if not isinstance(cuerpo, dict):
        raise ReporteInvalido("el mensaje no es un objeto")
    incidencia_id = cuerpo.get("incidenciaId")
    usuario_id = cuerpo.get("usuarioId")
    reporte = cuerpo.get("reporte")
    if not incidencia_id or not usuario_id or not isinstance(reporte, dict):
        raise ReporteInvalido("faltan incidenciaId, usuarioId o reporte")
    faltan = [c for c in OBLIGATORIOS if not reporte.get(c)]
    if faltan:
        raise ReporteInvalido(f"faltan campos: {', '.join(faltan)}")
    if reporte["tipo"] not in TIPOS:
        raise ReporteInvalido(f"tipo inválido: {reporte['tipo']}")
    if len(str(reporte["descripcion"])) > 1000:
        raise ReporteInvalido("descripcion supera 1000 caracteres")
    for campo in ("lat", "lon"):
        if reporte.get(campo) is not None and not isinstance(reporte[campo], (int, float)):
            raise ReporteInvalido(f"{campo} debe ser numérico")
    incidencia = {
        "incidenciaId": str(incidencia_id),
        "usuarioId": str(usuario_id),
        "clase": "INDIVIDUAL",
        "origen": "USUARIO",
    }
    for campo in (*OBLIGATORIOS, *OPCIONALES):
        if reporte.get(campo) is not None:
            incidencia[campo] = reporte[campo]
    return incidencia


def contar_ventana(zona_id: str, ref: datetime) -> tuple[set[str], list[str], str]:
    """Usuarios distintos, ids de incidencia y ts mínimo de las últimas 24 h."""
    desde = f"R#{iso(ref - timedelta(hours=eventos.VENTANA_COLECTIVO_HORAS))}"
    hasta = f"R#{iso(ref)}~"  # '~' > '#': incluye los ítems con el mismo ts
    usuarios, incidencias, ts_min = set(), [], iso(ref)
    kwargs = {
        "TableName": nombres.tabla("zona-ventana"),
        "KeyConditionExpression": "zonaId = :z AND sk BETWEEN :d AND :h",
        "ExpressionAttributeValues": {":z": {"S": zona_id}, ":d": {"S": desde}, ":h": {"S": hasta}},
        "ConsistentRead": True,
    }
    while True:
        r = cliente().query(**kwargs)
        for it in r["Items"]:
            usuarios.add(it["usuarioId"]["S"])
            incidencias.append(it["incidenciaId"]["S"])
            ts_min = min(ts_min, it["sk"]["S"].split("#")[1])
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return usuarios, incidencias, ts_min


def obtener_marcador(zona_id: str) -> dict | None:
    r = cliente().get_item(
        TableName=nombres.tabla("zona-ventana"),
        Key={"zonaId": {"S": zona_id}, "sk": {"S": MARCADOR}},
        ConsistentRead=True,
    )
    return {k: _des.deserialize(v) for k, v in r["Item"].items()} if "Item" in r else None


def crear_marcador(zona_id: str, usuarios: int, inicio: str, fin: str, incidencias: list[str], ref: datetime) -> str | None:
    """Crea el marcador de forma condicional. Devuelve el id del colectivo o None si ya existía."""
    colectivo_id = f"COL-{uuid.uuid4().hex.upper()}"
    item = {
        "zonaId": zona_id,
        "sk": MARCADOR,
        "incidenteColectivoId": colectivo_id,
        "usuariosDistintos": usuarios,
        "ventanaInicio": inicio,
        "ventanaFin": fin,
        "incidencias": incidencias,
        "ttl": int((ref + timedelta(hours=eventos.VENTANA_COLECTIVO_HORAS)).timestamp()),
    }
    try:
        cliente().put_item(
            TableName=nombres.tabla("zona-ventana"),
            Item=_item(item),
            ConditionExpression="attribute_not_exists(sk)",
        )
    except cliente().exceptions.ConditionalCheckFailedException:
        return None
    return colectivo_id


def _camino(origen: str, destino: str) -> list[str]:
    """Camino más corto entre dos estados según la máquina de estados (BFS)."""
    if origen == destino:
        return []
    cola, vistos = deque([(Estado(origen), [])]), {Estado(origen)}
    while cola:
        actual, ruta = cola.popleft()
        for sig in sorted(TRANSICIONES[actual]):
            if sig in vistos:
                continue
            if sig == Estado(destino):
                return [*ruta, str(sig)]
            vistos.add(sig)
            cola.append((sig, [*ruta, str(sig)]))
    raise TransicionInvalida(f"{origen} → {destino} sin camino")


def agrupar(incidencia_id: str, colectivo_id: str) -> None:
    """Asocia la incidencia al colectivo y la lleva al mismo estado que el colectivo."""
    try:
        estado_colectivo = obtener(colectivo_id, cliente=cliente())["estado"]
    except IncidenciaNoEncontrada:
        estado_colectivo = None  # el colectivo aún no existe (lo crea la SFN `colectivo`)
    actual = obtener(incidencia_id, cliente=cliente())
    ruta = _camino(actual["estado"], estado_colectivo) if estado_colectivo else []
    if not ruta:
        # Sin cambio de estado posible: solo se anota el vínculo (no es una transición).
        cliente().update_item(
            TableName=nombres.tabla("incidencias"),
            Key={"incidenciaId": {"S": incidencia_id}},
            UpdateExpression="SET incidenteColectivoId = :c, actualizadoEn = :t ADD #v :uno",
            ConditionExpression="attribute_exists(incidenciaId)",
            ExpressionAttributeNames={"#v": "version"},
            ExpressionAttributeValues={":c": {"S": colectivo_id}, ":t": {"S": iso(ahora())}, ":uno": {"N": "1"}},
        )
        return
    for i, estado in enumerate(ruta):
        transicionar(
            incidencia_id,
            estado,
            "SISTEMA",
            comentario=f"Agrupada en {colectivo_id}",
            cambios={"incidenteColectivoId": colectivo_id} if i == 0 else None,
            cliente=cliente(),
        )


def procesar(cuerpo: dict) -> None:
    incidencia = validar(cuerpo)
    ref = ahora()
    nueva = registrar_inicial(incidencia, cliente=cliente())
    zona_id, usuario_id, inc_id = incidencia["zonaId"], incidencia["usuarioId"], incidencia["incidenciaId"]

    # Ítem de la ventana.
    cliente().put_item(
        TableName=nombres.tabla("zona-ventana"),
        Item=_item(
            {
                "zonaId": zona_id,
                "sk": f"R#{iso(ref)}#{usuario_id}",
                "incidenciaId": inc_id,
                "usuarioId": usuario_id,
                "tipo": incidencia["tipo"],
                "ttl": int((ref + timedelta(hours=eventos.VENTANA_COLECTIVO_HORAS)).timestamp()),
            }
        ),
    )
    if not nueva:
        # Reintento de SQS: la incidencia ya estaba; no se vuelve a agrupar si ya avanzó.
        previa = obtener(inc_id, cliente=cliente())
        if previa.get("incidenteColectivoId") or previa["estado"] != str(Estado.REGISTRADA):
            return

    marcador = obtener_marcador(zona_id)
    if marcador is None:
        usuarios, incidencias, inicio = contar_ventana(zona_id, ref)
        if len(usuarios) < eventos.USUARIOS_UMBRAL_COLECTIVO:
            return
        if crear_marcador(zona_id, len(usuarios), inicio, iso(ref), incidencias, ref):
            return  # este reporte disparó el colectivo; la SFN agrupa a los ya existentes
        marcador = obtener_marcador(zona_id)  # carrera: otro lote lo creó primero
        if marcador is None:
            return
    agrupar(inc_id, marcador["incidenteColectivoId"])


def lambda_handler(event, context):
    fallidos = []
    for registro in event.get("Records", []):
        try:
            procesar(json.loads(registro["body"]))
        except Exception:  # noqa: BLE001 - cada mensaje falla por separado
            log.exception("Fallo procesando mensaje %s", registro.get("messageId"))
            fallidos.append({"itemIdentifier": registro["messageId"]})
    return {"batchItemFailures": fallidos}
