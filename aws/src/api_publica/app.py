"""API pública de reportes (sin login): solo clientes con contrato registrado.

Rutas (contratos/openapi.yaml):
- `POST /reportes`           registra una falla. El cliente se identifica con su DNI o su número
                             de servicio; zona y contacto salen del contrato (tabla `clientes`),
                             nunca del cuerpo. Encola en SQS el cuerpo de integraciones.md §1.
- `POST /reportes/consulta`  DNI + número de servicio → reportes de ese servicio con su historial,
                             sin PII.
- `POST /incidencias`        (autenticado, compatibilidad) misma lógica que `POST /reportes`.

Errores: 400 validación, 404 `CLIENTE_NO_ENCONTRADO`, 409 `VARIOS_SERVICIOS`,
403 `CONTRATO_SUSPENDIDO`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeDeserializer

from telecorp_comun import nombres

log = logging.getLogger()
log.setLevel(logging.INFO)

TIPOS = {"SIN_INTERNET", "SIN_LLAMADAS", "SIN_SMS", "LENTITUD", "INTERMITENCIA", "OTRO"}
RE_DOCUMENTO = re.compile(r"^\d{8}$")
RE_SERVICIO = re.compile(r"^SRV-\d{8}$")
RE_SERVICIO_ENMASCARADO = re.compile(r"^SRV-\*{4}(\d{4})$")
MSG_NO_ENCONTRADO = "No encontramos un servicio con esos datos"
LIMITE_CONSULTA = 50

# Campos de la incidencia que se devuelven en la consulta pública (sin PII).
CAMPOS_PUBLICOS = (
    "incidenciaId", "estado", "zonaId", "tipo", "descripcion", "fechaHoraEvento",
    "nivelPrioridad", "incidenteColectivoId", "creadoEn", "actualizadoEn", "resueltoEn",
)

CABECERAS = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}

_des = TypeDeserializer()
_ddb = None
_sqs = None


class ErrorHttp(Exception):
    def __init__(self, status: int, codigo: str, mensaje: str, extra: dict | None = None):
        super().__init__(mensaje)
        self.status, self.codigo, self.mensaje, self.extra = status, codigo, mensaje, extra or {}


def ddb():
    global _ddb
    if _ddb is None:
        _ddb = boto3.client("dynamodb")
    return _ddb


def sqs():
    global _sqs
    if _sqs is None:
        _sqs = boto3.client("sqs")
    return _sqs


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError(type(o))


def respuesta(status: int, cuerpo) -> dict:
    return {"statusCode": status, "headers": CABECERAS, "body": json.dumps(cuerpo, default=_json_default, ensure_ascii=False)}


def _py(item: dict) -> dict:
    return {k: _des.deserialize(v) for k, v in item.items()}


def enmascarar(numero: str) -> str:
    return f"SRV-****{numero[-4:]}"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cuerpo(event: dict) -> dict:
    try:
        cuerpo = json.loads(event.get("body") or "")
    except ValueError:
        raise ErrorHttp(400, "CUERPO_INVALIDO", "El cuerpo debe ser JSON") from None
    if not isinstance(cuerpo, dict):
        raise ErrorHttp(400, "CUERPO_INVALIDO", "El cuerpo debe ser un objeto")
    return cuerpo


def _texto(cuerpo: dict, campo: str) -> str | None:
    v = cuerpo.get(campo)
    if v is None or v == "":
        return None
    if not isinstance(v, str):
        raise ErrorHttp(400, "CAMPO_INVALIDO", f"{campo} debe ser texto")
    return v.strip().upper() if campo == "numeroServicio" else v.strip()


# ---------- contratos ----------

def contrato_por_servicio(numero: str) -> dict | None:
    r = ddb().get_item(TableName=nombres.tabla("clientes"), Key={"numeroServicio": {"S": numero}})
    return _py(r["Item"]) if "Item" in r else None


def contratos_por_documento(documento: str) -> list[dict]:
    kwargs = {
        "TableName": nombres.tabla("clientes"),
        "IndexName": "por-documento",
        "KeyConditionExpression": "documento = :d",
        "ExpressionAttributeValues": {":d": {"S": documento}},
    }
    items = []
    while True:
        r = ddb().query(**kwargs)
        items.extend(_py(i) for i in r["Items"])
        if "LastEvaluatedKey" not in r:
            return items
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def nombre_zona(zona_id: str) -> str:
    try:
        r = ddb().get_item(TableName=nombres.tabla("zonas"), Key={"zonaId": {"S": zona_id}})
        return r.get("Item", {}).get("nombre", {}).get("S") or zona_id
    except Exception:  # noqa: BLE001 - el nombre es decorativo
        log.warning("No se pudo leer la zona %s", zona_id)
        return zona_id


def resolver_contrato(documento: str | None, numero: str | None) -> dict:
    """Busca el contrato. Lanza 404 / 409 / 403 según contratos/openapi.yaml."""
    if documento is not None and not RE_DOCUMENTO.match(documento):
        raise ErrorHttp(400, "DOCUMENTO_INVALIDO", "El DNI debe tener 8 dígitos")
    if numero is not None and not (RE_SERVICIO.match(numero) or RE_SERVICIO_ENMASCARADO.match(numero)):
        raise ErrorHttp(400, "SERVICIO_INVALIDO", "El número de servicio tiene el formato SRV-12345678")
    if not documento and not numero:
        raise ErrorHttp(400, "IDENTIFICACION_REQUERIDA", "Indica tu DNI o tu número de servicio")

    enmascarado = RE_SERVICIO_ENMASCARADO.match(numero) if numero else None
    if numero and not enmascarado:
        contrato = contrato_por_servicio(numero)
        if not contrato or (documento and contrato.get("documento") != documento):
            raise ErrorHttp(404, "CLIENTE_NO_ENCONTRADO", MSG_NO_ENCONTRADO)
    else:
        if not documento:  # número enmascarado sin DNI
            raise ErrorHttp(400, "IDENTIFICACION_REQUERIDA", "Indica tu DNI junto con el servicio elegido")
        contratos = contratos_por_documento(documento)
        if enmascarado:
            contratos = [c for c in contratos if c["numeroServicio"].endswith(enmascarado.group(1))]
        if not contratos:
            raise ErrorHttp(404, "CLIENTE_NO_ENCONTRADO", MSG_NO_ENCONTRADO)
        if len(contratos) > 1:
            servicios = [
                {"numeroServicio": enmascarar(c["numeroServicio"]), "zonaId": c["zonaId"], "zonaNombre": nombre_zona(c["zonaId"])}
                for c in sorted(contratos, key=lambda c: c["numeroServicio"])
            ]
            raise ErrorHttp(
                409, "VARIOS_SERVICIOS", "Tienes varios servicios: elige cuál tiene la falla", {"servicios": servicios}
            )
        contrato = contratos[0]

    if contrato.get("estadoContrato") != "ACTIVO":
        raise ErrorHttp(403, "CONTRATO_SUSPENDIDO", "Tu servicio está suspendido; comunícate con atención al cliente")
    return contrato


# ---------- POST /reportes ----------

def _fecha_evento(valor, ahora: datetime) -> str:
    if valor is None or valor == "":
        return _iso(ahora)
    if not isinstance(valor, str):
        raise ErrorHttp(400, "FECHA_INVALIDA", "fechaHoraEvento debe ser ISO 8601")
    try:
        dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        raise ErrorHttp(400, "FECHA_INVALIDA", "fechaHoraEvento debe ser ISO 8601") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt > ahora + timedelta(minutes=5):
        raise ErrorHttp(400, "FECHA_INVALIDA", "La fecha no puede estar en el futuro")
    return _iso(dt)


def registrar(event: dict) -> dict:
    cuerpo = _cuerpo(event)
    tipo, descripcion = cuerpo.get("tipo"), cuerpo.get("descripcion")
    if tipo not in TIPOS:
        raise ErrorHttp(400, "TIPO_INVALIDO", "tipo no válido")
    if not isinstance(descripcion, str) or not descripcion.strip() or len(descripcion) > 1000:
        raise ErrorHttp(400, "DESCRIPCION_INVALIDA", "La descripción es obligatoria (hasta 1000 caracteres)")
    ahora = datetime.now(timezone.utc)
    fecha = _fecha_evento(cuerpo.get("fechaHoraEvento"), ahora)

    contrato = resolver_contrato(_texto(cuerpo, "documento"), _texto(cuerpo, "numeroServicio"))
    numero = contrato["numeroServicio"]
    incidencia_id = str(uuid.uuid4())
    # Cuerpo EXACTO de contratos/integraciones.md §1. Zona y contacto vienen del contrato.
    reporte = {
        "documento": contrato["documento"],
        "numeroServicio": numero,
        "telefono": contrato.get("telefono"),
        "email": contrato.get("email"),
        "zonaId": contrato["zonaId"],
        "fechaHoraEvento": fecha,
        "tipo": tipo,
        "descripcion": descripcion.strip(),
    }
    mensaje = {
        "incidenciaId": incidencia_id,
        "usuarioId": f"cliente:{numero}",
        "recibidoEn": int(time.time() * 1000),
        "reporte": {k: v for k, v in reporte.items() if v is not None},
    }
    cola = os.environ.get("COLA_REPORTES_URL")
    if not cola:
        raise ErrorHttp(500, "CONFIGURACION", "Falta COLA_REPORTES_URL")
    try:
        sqs().send_message(QueueUrl=cola, MessageBody=json.dumps(mensaje, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        log.exception("No se pudo encolar el reporte")
        raise ErrorHttp(502, "COLA_NO_DISPONIBLE", "No se pudo registrar el reporte") from None
    return respuesta(
        202,
        {
            "incidenciaId": incidencia_id,
            "estado": "REGISTRADA",
            "zonaId": contrato["zonaId"],
            "zonaNombre": nombre_zona(contrato["zonaId"]),
        },
    )


# ---------- POST /reportes/consulta ----------

def _historial(incidencia_id: str) -> list[dict]:
    kwargs = {
        "TableName": nombres.tabla("historial-estados"),
        "KeyConditionExpression": "incidenciaId = :i",
        "ExpressionAttributeValues": {":i": {"S": incidencia_id}},
    }
    filas = []
    while True:
        r = ddb().query(**kwargs)
        filas.extend(_py(i) for i in r["Items"])
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    salida = []
    for f in filas:
        actor = f.get("actor", "SISTEMA")
        salida.append(
            {
                "estadoAnterior": f.get("estadoAnterior"),
                "estadoNuevo": f["estadoNuevo"],
                "actor": "OPERADOR" if actor.startswith("OPERADOR") else "SISTEMA",
                "comentario": f.get("comentario"),
                "ts": f["ts"],
            }
        )
    return salida


def consultar(event: dict) -> dict:
    cuerpo = _cuerpo(event)
    documento, numero = _texto(cuerpo, "documento"), _texto(cuerpo, "numeroServicio")
    if not documento or not numero:
        raise ErrorHttp(400, "IDENTIFICACION_REQUERIDA", "Indica tu DNI y tu número de servicio")
    contrato = None
    if RE_DOCUMENTO.match(documento) and RE_SERVICIO.match(numero):
        contrato = contrato_por_servicio(numero)
    if not contrato or contrato.get("documento") != documento:
        raise ErrorHttp(404, "CLIENTE_NO_ENCONTRADO", MSG_NO_ENCONTRADO)

    r = ddb().query(
        TableName=nombres.tabla("incidencias"),
        IndexName="por-usuario",
        KeyConditionExpression="usuarioId = :u",
        ExpressionAttributeValues={":u": {"S": f"cliente:{numero}"}},
        ScanIndexForward=False,
        Limit=LIMITE_CONSULTA,
    )
    reportes = []
    for item in (_py(i) for i in r["Items"]):
        publico = {c: item[c] for c in CAMPOS_PUBLICOS if item.get(c) is not None}
        publico["historial"] = _historial(item["incidenciaId"])
        reportes.append(publico)
    return respuesta(
        200,
        {
            "servicio": enmascarar(numero),
            "zonaId": contrato["zonaId"],
            "zonaNombre": nombre_zona(contrato["zonaId"]),
            "reportes": reportes,
        },
    )


# ---------- enrutador ----------

def despachar(event: dict) -> dict:
    ruta = (event.get("resource"), event.get("httpMethod"))
    if ruta in (("/reportes", "POST"), ("/incidencias", "POST")):
        return registrar(event)
    if ruta == ("/reportes/consulta", "POST"):
        return consultar(event)
    raise ErrorHttp(404, "RUTA_DESCONOCIDA", f"{ruta[1]} {ruta[0]} no existe")


def lambda_handler(event, context):
    try:
        return despachar(event)
    except ErrorHttp as e:
        return respuesta(e.status, {"codigo": e.codigo, "mensaje": e.mensaje, **e.extra})
    except Exception:  # noqa: BLE001
        log.exception("Error no controlado")
        return respuesta(500, {"codigo": "ERROR_INTERNO", "mensaje": "Error interno"})
