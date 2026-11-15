"""API de incidencias (proxy de API Gateway REST con autorizador de Cognito).

Rutas (contratos/openapi.yaml): GET /incidencias, GET /incidencias/{id},
GET /incidencias/{id}/historial, PATCH /incidencias/{id}/estado, GET /zonas, GET /antenas.
`POST /incidencias` NO pasa por aquí: API Gateway lo envía directo a SQS.

Autorización: los usuarios solo ven sus incidencias (por `sub`); el grupo `operadores` ve todo
y es el único que cambia estados (vía `transicionar()`) y consulta antenas.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from telecorp_comun import nombres
from telecorp_comun.estados import (
    Estado,
    IncidenciaNoEncontrada,
    TransicionInvalida,
    obtener,
    transicionar,
)

log = logging.getLogger()
log.setLevel(logging.INFO)

GRUPO_OPERADORES = "operadores"
LIMITE_DEFECTO, LIMITE_MAXIMO = 50, 200

CAMPOS_INCIDENCIA = (
    "incidenciaId", "clase", "origen", "estado", "zonaId", "tipo", "descripcion", "fechaHoraEvento",
    "tipoIA", "severidadIA", "resumenIA", "prioridad", "nivelPrioridad", "incidenteColectivoId",
    "usuariosAfectados", "antenasAfectadas", "creadoEn", "actualizadoEn", "resueltoEn",
)
CAMPOS_ZONA = ("zonaId", "nombre", "tipoZona", "ciudad", "lat", "lon")
CAMPOS_ANTENA = ("antenaId", "zonaId", "estado", "gbPorMinuto", "ultimaLectura")
ESTADOS_ANTENA = {"NORMAL", "CAIDA", "SATURADA", "INHABILITADA"}

_ser = TypeSerializer()
_des = TypeDeserializer()
_cliente = None

CABECERAS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}


class ErrorHttp(Exception):
    def __init__(self, status: int, codigo: str, mensaje: str):
        super().__init__(mensaje)
        self.status, self.codigo, self.mensaje = status, codigo, mensaje


def cliente():
    global _cliente
    if _cliente is None:
        _cliente = boto3.client("dynamodb")
    return _cliente


# ---------- utilidades ----------

def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(type(o))


def respuesta(status: int, cuerpo) -> dict:
    return {"statusCode": status, "headers": CABECERAS, "body": json.dumps(cuerpo, default=_json_default)}


def error(status: int, codigo: str, mensaje: str) -> dict:
    return respuesta(status, {"codigo": codigo, "mensaje": mensaje})


def identidad(event: dict) -> tuple[str | None, bool]:
    """(sub, es_operador) a partir de los claims del autorizador de Cognito."""
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    grupos = claims.get("cognito:groups") or []
    if isinstance(grupos, str):
        grupos = [g for g in re.split(r"[\s,\[\]]+", grupos) if g]
    return claims.get("sub"), GRUPO_OPERADORES in grupos


def _a_python(item: dict) -> dict:
    return {k: _des.deserialize(v) for k, v in item.items()}


def _proyectar(item: dict, campos) -> dict:
    return {c: item[c] for c in campos if item.get(c) is not None}


def _cursor_a_texto(clave: dict | None) -> str | None:
    return base64.urlsafe_b64encode(json.dumps(clave).encode()).decode() if clave else None


def _texto_a_cursor(texto: str | None) -> dict | None:
    if not texto:
        return None
    try:
        clave = json.loads(base64.urlsafe_b64decode(texto.encode()))
        if not isinstance(clave, dict):
            raise ValueError
        return clave
    except (ValueError, TypeError):
        raise ErrorHttp(400, "CURSOR_INVALIDO", "El cursor no es válido") from None


def _limite(params: dict) -> int:
    try:
        n = int(params.get("limite") or LIMITE_DEFECTO)
    except ValueError:
        raise ErrorHttp(400, "LIMITE_INVALIDO", "limite debe ser un entero") from None
    if n < 1:
        raise ErrorHttp(400, "LIMITE_INVALIDO", "limite debe ser positivo")
    return min(n, LIMITE_MAXIMO)


# ---------- consultas ----------

def listar_incidencias(params: dict, sub: str, operador: bool) -> dict:
    limite = _limite(params)
    if params.get("estado") and params["estado"] not in {str(e) for e in Estado}:
        raise ErrorHttp(400, "ESTADO_INVALIDO", "estado no válido")
    if params.get("clase") and params["clase"] not in ("INDIVIDUAL", "COLECTIVA"):
        raise ErrorHttp(400, "CLASE_INVALIDA", "clase no válida")

    cond, valores, nombres_attr, filtros = [], {}, {}, []
    # Elección del índice: el usuario normal siempre por `por-usuario`.
    if not operador:
        indice, pk, valor_pk = "por-usuario", "usuarioId", sub
    elif params.get("estado"):
        indice, pk, valor_pk = "por-estado", "estado", params["estado"]
    elif params.get("zonaId"):
        indice, pk, valor_pk = "por-zona", "zonaId", params["zonaId"]
    else:
        indice = pk = valor_pk = None

    if indice:
        cond.append("#pk = :pk")
        nombres_attr["#pk"] = pk
        valores[":pk"] = {"S": valor_pk}
        if params.get("desde"):
            cond.append("creadoEn >= :desde")
            valores[":desde"] = {"S": params["desde"]}
    elif params.get("desde"):
        filtros.append("creadoEn >= :desde")
        valores[":desde"] = {"S": params["desde"]}

    for campo in ("zonaId", "estado", "clase"):
        if params.get(campo) and campo != pk:
            filtros.append(f"#f_{campo} = :f_{campo}")
            nombres_attr[f"#f_{campo}"] = campo
            valores[f":f_{campo}"] = {"S": params[campo]}

    kwargs: dict = {"TableName": nombres.tabla("incidencias"), "Limit": limite}
    if indice:
        kwargs["IndexName"] = indice
        kwargs["KeyConditionExpression"] = " AND ".join(cond)
        kwargs["ScanIndexForward"] = False
    if filtros:
        kwargs["FilterExpression"] = " AND ".join(filtros)
    if nombres_attr:
        kwargs["ExpressionAttributeNames"] = nombres_attr
    if valores:
        kwargs["ExpressionAttributeValues"] = valores
    inicio = _texto_a_cursor(params.get("cursor"))
    if inicio:
        kwargs["ExclusiveStartKey"] = inicio

    items, ultima = [], None
    while True:
        r = cliente().query(**kwargs) if indice else cliente().scan(**kwargs)
        items.extend(_a_python(i) for i in r["Items"])
        ultima = r.get("LastEvaluatedKey")
        if not ultima or len(items) >= limite:
            break
        kwargs["ExclusiveStartKey"] = ultima
        kwargs["Limit"] = limite - len(items)
    if len(items) > limite:
        items = items[:limite]
    return {"items": [_proyectar(i, CAMPOS_INCIDENCIA) for i in items], "cursor": _cursor_a_texto(ultima)}


def incidencia_visible(incidencia_id: str, sub: str, operador: bool) -> dict:
    """Devuelve la incidencia o 404 (también cuando es ajena: no se revela su existencia)."""
    try:
        inc = obtener(incidencia_id, cliente=cliente())
    except IncidenciaNoEncontrada:
        raise ErrorHttp(404, "NO_ENCONTRADA", "Incidencia no encontrada") from None
    if not operador and inc.get("usuarioId") != sub:
        raise ErrorHttp(404, "NO_ENCONTRADA", "Incidencia no encontrada")
    return inc


def historial(incidencia_id: str, operador: bool) -> list[dict]:
    kwargs = {
        "TableName": nombres.tabla("historial-estados"),
        "KeyConditionExpression": "incidenciaId = :i",
        "ExpressionAttributeValues": {":i": {"S": incidencia_id}},
        "ConsistentRead": True,
    }
    filas = []
    while True:
        r = cliente().query(**kwargs)
        filas.extend(_a_python(i) for i in r["Items"])
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    salida = []
    for f in filas:
        actor = f.get("actor", "SISTEMA")
        if not operador and actor.startswith("OPERADOR:"):
            actor = "OPERADOR"  # no se expone el sub de los operadores
        salida.append(
            {
                "estadoAnterior": f.get("estadoAnterior"),
                "estadoNuevo": f["estadoNuevo"],
                "actor": actor,
                "comentario": f.get("comentario"),
                "ts": f["ts"],
            }
        )
    return salida


def cambiar_estado(incidencia_id: str, cuerpo_json: str | None, sub: str, operador: bool) -> dict:
    if not operador:
        raise ErrorHttp(403, "PROHIBIDO", "Solo los operadores pueden cambiar el estado")
    try:
        cuerpo = json.loads(cuerpo_json or "")
    except ValueError:
        raise ErrorHttp(400, "CUERPO_INVALIDO", "El cuerpo debe ser JSON") from None
    if not isinstance(cuerpo, dict):
        raise ErrorHttp(400, "CUERPO_INVALIDO", "El cuerpo debe ser un objeto")
    nuevo, comentario = cuerpo.get("estadoNuevo"), cuerpo.get("comentario")
    if nuevo not in {str(e) for e in Estado}:
        raise ErrorHttp(400, "ESTADO_INVALIDO", "estadoNuevo no es un estado válido")
    if comentario is not None and (not isinstance(comentario, str) or len(comentario) > 500):
        raise ErrorHttp(400, "COMENTARIO_INVALIDO", "comentario debe ser texto de hasta 500 caracteres")
    try:
        actualizada = transicionar(incidencia_id, nuevo, f"OPERADOR:{sub}", comentario, cliente=cliente())
    except IncidenciaNoEncontrada:
        raise ErrorHttp(404, "NO_ENCONTRADA", "Incidencia no encontrada") from None
    except TransicionInvalida as e:
        raise ErrorHttp(409, "TRANSICION_INVALIDA", str(e)) from None
    except cliente().exceptions.TransactionCanceledException:
        raise ErrorHttp(409, "CONFLICTO", "La incidencia cambió mientras se actualizaba; reintente") from None
    return _proyectar(actualizada, CAMPOS_INCIDENCIA)


def escanear(tabla: str, **extra) -> list[dict]:
    kwargs = {"TableName": nombres.tabla(tabla), **extra}
    items = []
    while True:
        r = cliente().scan(**kwargs)
        items.extend(_a_python(i) for i in r["Items"])
        if "LastEvaluatedKey" not in r:
            return items
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def listar_antenas(params: dict) -> list[dict]:
    estado, zona = params.get("estado"), params.get("zonaId")
    if estado and estado not in ESTADOS_ANTENA:
        raise ErrorHttp(400, "ESTADO_INVALIDO", "estado no válido")
    if zona:
        kwargs = {
            "TableName": nombres.tabla("antenas"),
            "IndexName": "por-zona",
            "KeyConditionExpression": "zonaId = :z",
            "ExpressionAttributeValues": {":z": {"S": zona}},
        }
        items = []
        while True:
            r = cliente().query(**kwargs)
            items.extend(_a_python(i) for i in r["Items"])
            if "LastEvaluatedKey" not in r:
                break
            kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    else:
        items = escanear("antenas")
    if estado:
        items = [i for i in items if i.get("estado") == estado]
    return [_proyectar(i, CAMPOS_ANTENA) for i in items]


# ---------- enrutador ----------

def despachar(event: dict) -> dict:
    recurso, metodo = event.get("resource"), event.get("httpMethod")
    params = event.get("queryStringParameters") or {}
    ruta = event.get("pathParameters") or {}
    sub, operador = identidad(event)

    if (recurso, metodo) == ("/zonas", "GET"):  # público
        zonas = escanear("zonas")
        return respuesta(200, [_proyectar(z, CAMPOS_ZONA) for z in zonas])

    if not sub:
        raise ErrorHttp(401, "NO_AUTENTICADO", "Falta la identidad del usuario")

    if (recurso, metodo) == ("/incidencias", "GET"):
        return respuesta(200, listar_incidencias(params, sub, operador))
    if (recurso, metodo) == ("/incidencias/{incidenciaId}", "GET"):
        return respuesta(200, _proyectar(incidencia_visible(ruta["incidenciaId"], sub, operador), CAMPOS_INCIDENCIA))
    if (recurso, metodo) == ("/incidencias/{incidenciaId}/historial", "GET"):
        incidencia_visible(ruta["incidenciaId"], sub, operador)
        return respuesta(200, historial(ruta["incidenciaId"], operador))
    if (recurso, metodo) == ("/incidencias/{incidenciaId}/estado", "PATCH"):
        return respuesta(200, cambiar_estado(ruta["incidenciaId"], event.get("body"), sub, operador))
    if (recurso, metodo) == ("/antenas", "GET"):
        if not operador:
            raise ErrorHttp(403, "PROHIBIDO", "Solo los operadores consultan antenas")
        return respuesta(200, listar_antenas(params))
    raise ErrorHttp(404, "RUTA_DESCONOCIDA", f"{metodo} {recurso} no existe")


def lambda_handler(event, context):
    try:
        return despachar(event)
    except ErrorHttp as e:
        return error(e.status, e.codigo, e.mensaje)
    except Exception:  # noqa: BLE001
        log.exception("Error no controlado")
        return error(500, "ERROR_INTERNO", "Error interno")
