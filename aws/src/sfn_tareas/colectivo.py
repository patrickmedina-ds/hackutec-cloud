"""Tareas de la SFN `colectivo`. Se elige la acción con `event["accion"]`.

- CREAR_COLECTIVA: umbral de 10 usuarios superado. Crea la COLECTIVA y la pasa a
  EN_REVISION (o ATENDIDA si hay antena degradada en la zona).
- MOVER_HIJO: lleva un reporte hijo al estado del colectivo y le asigna
  `incidenteColectivoId`. Ignora transiciones inválidas.
- CREAR_PROACTIVA: antena degradada sin colectivo activo. Reclama el marcador
  `COLECTIVO_ACTIVO` de la zona y crea la incidencia `ANT-<ulid>` en ATENDIDA.
- ESCALAR: antena degradada con colectivo activo. Si el colectivo está en
  EN_REVISION lo pasa a ATENDIDA y devuelve sus hijos.
- RESTABLECER: todas las antenas de la zona volvieron a NORMAL. Pasa el
  colectivo a SOLUCIONADA, borra el marcador y devuelve sus hijos.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

import boto3
from botocore.exceptions import ClientError
from telecorp_comun import nombres, prioridad
from telecorp_comun.antenas import EstadoAntena, es_degradada
from telecorp_comun.estados import Estado, IncidenciaNoEncontrada, obtener, registrar_inicial
from telecorp_comun.eventos import VENTANA_COLECTIVO_HORAS

import transiciones
from util import TIPO_ZONA_POR_DEFECTO, iso, leer_zona, logger, parsear_fecha, severidad_valida, ulid_determinista
from util import hijos_de_colectivo

ACTOR = "STEPFN:colectivo"
MARCADOR = "COLECTIVO_ACTIVO"

# Severidad y tipo de falla que se asumen por estado de la antena.
SEVERIDAD_ANTENA = {
    EstadoAntena.INHABILITADA: "CRITICA",
    EstadoAntena.CAIDA: "ALTA",
    EstadoAntena.SATURADA: "MEDIA",
}
TIPO_ANTENA = {
    EstadoAntena.INHABILITADA: "SIN_INTERNET",
    EstadoAntena.CAIDA: "SIN_INTERNET",
    EstadoAntena.SATURADA: "LENTITUD",
}


def handler(event, context=None, *, cliente=None):
    cliente = cliente or boto3.client("dynamodb")
    accion = event.get("accion")
    funciones = {
        "CREAR_COLECTIVA": crear_colectiva,
        "MOVER_HIJO": mover_hijo,
        "CREAR_PROACTIVA": crear_proactiva,
        "ESCALAR": escalar,
        "RESTABLECER": restablecer,
    }
    if accion not in funciones:
        raise ValueError(f"Acción desconocida: {accion}")
    resultado = funciones[accion](event, cliente)
    logger.info("%s → %s", accion, resultado)
    return resultado


# --------------------------------------------------------------------------- umbral


def crear_colectiva(event: dict, cliente) -> dict:
    colectivo_id = event["incidenteColectivoId"]
    tipo_zona = event.get("tipoZona") or TIPO_ZONA_POR_DEFECTO
    ia = event.get("ia") or {}
    severidad = severidad_valida(ia.get("severidad"))
    antenas = event.get("antenas") or []
    degradadas = [a["antenaId"] for a in antenas if a.get("estado") and es_degradada(a["estado"])]
    usuarios = int(event.get("usuariosDistintos") or len(event.get("incidencias") or []))

    valor = prioridad.calcular(usuarios, tipo_zona, severidad, bool(degradadas))
    tipos = [t for t in (event.get("tipos") or []) if t]
    item = {
        "incidenciaId": colectivo_id,
        "clase": "COLECTIVA",
        "origen": "USUARIO",
        "zonaId": event["zonaId"],
        "tipo": Counter(tipos).most_common(1)[0][0] if tipos else None,
        "usuariosAfectados": usuarios,
        "antenasAfectadas": degradadas or None,
        "resumenIA": ia.get("resumen"),
        "severidadIA": severidad,
        "causaProbable": ia.get("causaProbable"),
        "prioridad": valor,
        "nivelPrioridad": prioridad.nivel(valor),
        "ventanaInicio": event.get("ventanaInicio"),
        "ventanaFin": event.get("ventanaFin"),
    }
    registrar_inicial({k: v for k, v in item.items() if v is not None}, ACTOR, cliente=cliente)

    destino = Estado.ATENDIDA if degradadas else Estado.EN_REVISION
    comentario = (
        "Incidencia identificada: antena degradada en la zona"
        if degradadas
        else f"Incidente colectivo: {usuarios} usuarios en {VENTANA_COLECTIVO_HORAS} h"
    )
    r = transiciones.mover(colectivo_id, destino, ACTOR, comentario, cliente=cliente)
    return {
        "incidenciaId": colectivo_id,
        "estado": r.get("estadoFinal", str(destino)),
        "destino": str(destino),
        "comentario": comentario,
        "prioridad": valor,
        "nivelPrioridad": prioridad.nivel(valor),
        "antenaDegradada": bool(degradadas),
    }


def mover_hijo(event: dict, cliente) -> dict:
    cambios = {"incidenteColectivoId": event.get("incidenteColectivoId")}
    return transiciones.mover(
        event["incidenciaId"],
        event["destino"],
        ACTOR,
        event.get("comentario"),
        {k: v for k, v in cambios.items() if v},
        cliente=cliente,
    )


# --------------------------------------------------------------------------- antenas


def _leer_marcador(zona_id: str, cliente) -> dict | None:
    r = cliente.get_item(
        TableName=nombres.tabla("zona-ventana"),
        Key={"zonaId": {"S": zona_id}, "sk": {"S": MARCADOR}},
        ConsistentRead=True,
    )
    item = r.get("Item")
    return {"incidenteColectivoId": item["incidenteColectivoId"]["S"]} if item else None


def crear_proactiva(event: dict, cliente) -> dict:
    antena_id, zona_id = event["antenaId"], event["zonaId"]
    estado_antena = event.get("estadoAntena") or EstadoAntena.CAIDA
    fecha = parsear_fecha(event.get("ts"))
    colectivo_id = "ANT-" + ulid_determinista(fecha, event.get("eventoId") or f"{antena_id}#{iso(fecha)}")

    # 1) Reclamar el marcador de la zona. El umbral (registrar-reporte) usa la misma
    #    condición, así que solo uno de los dos crea colectivo.
    inicio = fecha
    fin = inicio + timedelta(hours=VENTANA_COLECTIVO_HORAS)
    try:
        cliente.put_item(
            TableName=nombres.tabla("zona-ventana"),
            Item={
                "zonaId": {"S": zona_id},
                "sk": {"S": MARCADOR},
                "incidenteColectivoId": {"S": colectivo_id},
                "origen": {"S": "ANTENA"},
                "usuariosDistintos": {"N": "0"},
                "ventanaInicio": {"S": iso(inicio)},
                "ventanaFin": {"S": iso(fin)},
                "incidencias": {"L": []},
                "ttl": {"N": str(int(fin.timestamp()))},
            },
            ConditionExpression="attribute_not_exists(sk)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        existente = _leer_marcador(zona_id, cliente)
        if existente and existente["incidenteColectivoId"] != colectivo_id:
            # Otro colectivo ganó la carrera: la SFN agrega la antena a ese.
            return {"creada": False, "incidenciaId": existente["incidenteColectivoId"]}
        # Mismo id: es un reintento de esta misma ejecución; se continúa.

    # 2) Crear la incidencia proactiva (idempotente) y llevarla a ATENDIDA.
    zona = leer_zona(zona_id, cliente=cliente)
    tipo_zona = zona.get("tipoZona") or TIPO_ZONA_POR_DEFECTO
    usuarios = int(zona.get("usuariosEstimados") or 0)
    severidad = SEVERIDAD_ANTENA.get(estado_antena, "ALTA")
    valor = prioridad.calcular(usuarios, tipo_zona, severidad, True)
    registrar_inicial(
        {
            "incidenciaId": colectivo_id,
            "clase": "COLECTIVA",
            "origen": "ANTENA",
            "zonaId": zona_id,
            "tipo": TIPO_ANTENA.get(estado_antena, "OTRO"),
            "usuariosAfectados": usuarios,
            "antenasAfectadas": [antena_id],
            "severidadIA": severidad,
            "resumenIA": f"Antena {antena_id} en estado {estado_antena} en la zona {zona.get('nombre') or zona_id}.",
            "prioridad": valor,
            "nivelPrioridad": prioridad.nivel(valor),
            "creadoEn": iso(fecha),
        },
        ACTOR,
        cliente=cliente,
    )
    r = transiciones.mover(
        colectivo_id, Estado.ATENDIDA, ACTOR, f"Incidencia identificada: antena {antena_id} {estado_antena}", cliente=cliente
    )
    return {"creada": True, "incidenciaId": colectivo_id, "estado": r.get("estadoFinal"), "prioridad": valor}


def escalar(event: dict, cliente) -> dict:
    colectivo_id = event["incidenciaId"]
    try:
        colectivo = obtener(colectivo_id, cliente=cliente)
    except IncidenciaNoEncontrada:
        # El marcador existe pero la SFN de umbral aún no crea la incidencia;
        # esa ejecución verá la antena degradada al consultar la zona.
        return {"escalada": False, "motivo": "NO_ENCONTRADA", "hijos": []}
    if colectivo["estado"] != Estado.EN_REVISION:
        return {"escalada": False, "estado": colectivo["estado"], "hijos": []}

    zona = leer_zona(colectivo["zonaId"], cliente=cliente)
    valor = prioridad.calcular(
        int(colectivo.get("usuariosAfectados") or 0),
        zona.get("tipoZona") or TIPO_ZONA_POR_DEFECTO,
        colectivo.get("severidadIA"),
        True,
    )
    comentario = f"Incidencia identificada: antena {event.get('antenaId')} degradada"
    r = transiciones.mover(
        colectivo_id,
        Estado.ATENDIDA,
        ACTOR,
        comentario,
        {"prioridad": valor, "nivelPrioridad": prioridad.nivel(valor)},
        cliente=cliente,
    )
    return {
        "escalada": r["transicionado"],
        "estado": r.get("estadoFinal"),
        "destino": str(Estado.ATENDIDA),
        "comentario": comentario,
        "hijos": hijos_de_colectivo(colectivo_id, cliente=cliente) if r["transicionado"] else [],
    }


def restablecer(event: dict, cliente) -> dict:
    colectivo_id = event["incidenciaId"]
    comentario = "Servicio restablecido"
    try:
        colectivo = obtener(colectivo_id, cliente=cliente)
    except IncidenciaNoEncontrada:
        return {"restablecida": False, "motivo": "NO_ENCONTRADA", "hijos": []}
    if not colectivo.get("antenasAfectadas"):
        # Colectivo sin antena correlacionada: no lo cierra una antena ajena.
        return {"restablecida": False, "motivo": "SIN_ANTENAS", "hijos": []}

    r = transiciones.mover(colectivo_id, Estado.SOLUCIONADA, ACTOR, comentario, cliente=cliente)
    if r.get("estadoFinal") != Estado.SOLUCIONADA:
        return {"restablecida": False, "motivo": "SIN_CAMINO", "estado": r.get("estadoFinal"), "hijos": []}

    # El colectivo ya no está activo: se libera la zona para nuevos colectivos.
    try:
        cliente.delete_item(
            TableName=nombres.tabla("zona-ventana"),
            Key={"zonaId": {"S": colectivo["zonaId"]}, "sk": {"S": MARCADOR}},
            ConditionExpression="incidenteColectivoId = :c",
            ExpressionAttributeValues={":c": {"S": colectivo_id}},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
    return {
        "restablecida": True,
        "estado": str(Estado.SOLUCIONADA),
        "destino": str(Estado.SOLUCIONADA),
        "comentario": comentario,
        "hijos": hijos_de_colectivo(colectivo_id, cliente=cliente),
    }
