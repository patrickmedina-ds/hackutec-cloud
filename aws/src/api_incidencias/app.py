"""API de incidencias (proxy de API Gateway REST con autorizador de Cognito).

Rutas (contratos/openapi.yaml): GET /incidencias, GET /incidencias/{id},
GET /incidencias/{id}/historial, PATCH /incidencias/{id}/estado, GET /zonas, GET /antenas,
GET/POST /operadores y DELETE /operadores/{email} (gestión del grupo `operadores`; solo operadores).
`POST /incidencias` NO pasa por aquí: API Gateway lo envía directo a SQS.

Autorización: los usuarios solo ven sus incidencias (por `sub`); el grupo `operadores` ve todo
y es el único que cambia estados (vía `transicionar()`) y consulta antenas.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from decimal import Decimal
from urllib.parse import unquote

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
_cognito = None
RE_EMAIL = re.compile(r"^[^@\s\"\\]+@[^@\s\"\\]+\.[^@\s\"\\]+$")

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


def cognito():
    global _cognito
    if _cognito is None:
        _cognito = boto3.client("cognito-idp")
    return _cognito


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


def _claims(event: dict) -> dict:
    return ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}


def identidad(event: dict) -> tuple[str | None, bool]:
    """(sub, es_operador) a partir de los claims del autorizador de Cognito."""
    claims = _claims(event)
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


# ---------- operadores (grupo de Cognito) ----------

def _atributo(usuario: dict, nombre: str) -> str | None:
    for a in usuario.get("Attributes") or usuario.get("UserAttributes") or []:
        if a["Name"] == nombre:
            return a["Value"]
    return None


def _usuario_publico(u: dict) -> dict:
    creado = u.get("UserCreateDate")
    salida = {
        "email": _atributo(u, "email") or u.get("Username"),
        "nombre": _atributo(u, "name"),
        "creado": creado.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(creado, "strftime") else creado,
        "estado": u.get("UserStatus"),
    }
    return {k: v for k, v in salida.items() if v is not None}


def _pool() -> str:
    pool = os.environ.get("USER_POOL_ID")
    if not pool:
        raise ErrorHttp(500, "CONFIGURACION", "Falta USER_POOL_ID")
    return pool


def _miembros() -> list[dict]:
    paginas = cognito().get_paginator("list_users_in_group").paginate(UserPoolId=_pool(), GroupName=GRUPO_OPERADORES)
    return [u for p in paginas for u in p["Users"]]


def _email_valido(email) -> str:
    if not isinstance(email, str) or len(email) > 254 or not RE_EMAIL.match(email.strip()):
        raise ErrorHttp(400, "EMAIL_INVALIDO", "Indica un correo válido")
    return email.strip().lower()


def _buscar_usuario(email: str) -> dict | None:
    r = cognito().list_users(UserPoolId=_pool(), Filter=f'email = "{email}"', Limit=2)
    for u in r.get("Users", []):
        if (_atributo(u, "email") or "").lower() == email:
            return u
    return None


def _exigir_operador(operador: bool) -> None:
    if not operador:
        raise ErrorHttp(403, "PROHIBIDO", "Solo los operadores gestionan operadores")


def listar_operadores(operador: bool) -> list[dict]:
    _exigir_operador(operador)
    return sorted((_usuario_publico(u) for u in _miembros()), key=lambda u: u.get("email", ""))


def promover_operador(cuerpo_json: str | None, operador: bool) -> dict:
    _exigir_operador(operador)
    try:
        cuerpo = json.loads(cuerpo_json or "")
    except ValueError:
        raise ErrorHttp(400, "CUERPO_INVALIDO", "El cuerpo debe ser JSON") from None
    if not isinstance(cuerpo, dict):
        raise ErrorHttp(400, "CUERPO_INVALIDO", "El cuerpo debe ser un objeto")
    email = _email_valido(cuerpo.get("email"))
    usuario = _buscar_usuario(email)
    if not usuario:
        raise ErrorHttp(404, "USUARIO_NO_ENCONTRADO", "El usuario debe crear su cuenta primero")
    cognito().admin_add_user_to_group(UserPoolId=_pool(), Username=usuario["Username"], GroupName=GRUPO_OPERADORES)
    return _usuario_publico(usuario)


def quitar_operador(email_ruta: str, event: dict, operador: bool) -> dict:
    _exigir_operador(operador)
    email = _email_valido(email_ruta)
    claims = _claims(event)
    if email == (claims.get("email") or "").strip().lower():
        raise ErrorHttp(409, "AUTO_REVOCACION", "No puedes quitarte a ti mismo como operador")
    miembros = _miembros()
    objetivo = next((u for u in miembros if (_atributo(u, "email") or "").lower() == email), None)
    if not objetivo:
        raise ErrorHttp(404, "USUARIO_NO_ENCONTRADO", "Esa persona no es operadora")
    if claims.get("sub") and objetivo["Username"] == claims["sub"]:
        raise ErrorHttp(409, "AUTO_REVOCACION", "No puedes quitarte a ti mismo como operador")
    if len(miembros) <= 1:
        raise ErrorHttp(409, "ULTIMO_OPERADOR", "No se puede quitar al último operador")
    cognito().admin_remove_user_from_group(UserPoolId=_pool(), Username=objetivo["Username"], GroupName=GRUPO_OPERADORES)
    return _usuario_publico(objetivo)


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
    if (recurso, metodo) == ("/operadores", "GET"):
        return respuesta(200, listar_operadores(operador))
    if (recurso, metodo) == ("/operadores", "POST"):
        return respuesta(200, promover_operador(event.get("body"), operador))
    if (recurso, metodo) == ("/operadores/{email}", "DELETE"):
        return respuesta(200, quitar_operador(unquote(ruta.get("email", "")), event, operador))
    raise ErrorHttp(404, "RUTA_DESCONOCIDA", f"{metodo} {recurso} no existe")


def lambda_handler(event, context):
    try:
        return despachar(event)
    except ErrorHttp as e:
        return error(e.status, e.codigo, e.mensaje)
    except Exception:  # noqa: BLE001
        log.exception("Error no controlado")
        return error(500, "ERROR_INTERNO", "Error interno")
