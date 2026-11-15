"""Tarea `EvaluarAntena` de la SFN `recurrencia` (una invocación por antena).

Entrada: {"antenaId": "...", "zonaId": "...", "fechaReferencia": "2026-11-16T07:00:00Z"}

Cuenta los episodios de degradación (paso de NORMAL a no NORMAL) de los últimos
7 días en `metricas-antena`. Con 3 o más crea la incidencia
`REC-<antenaId>-<año>-W<semana ISO>` (COLECTIVA, origen RECURRENCIA, nivel mínimo
P2) y la pasa a EN_REVISION. Una por antena y semana: los reintentos y las
ejecuciones de otros días de la misma semana no la duplican.
"""

from __future__ import annotations

from datetime import timedelta

import boto3
from telecorp_comun import nombres, prioridad
from telecorp_comun.antenas import EstadoAntena
from telecorp_comun.estados import Estado, registrar_inicial

import transiciones
from util import TIPO_ZONA_POR_DEFECTO, ZONA_LIMA, iso, leer_zona, logger, parsear_fecha

ACTOR = "STEPFN:recurrencia"
DIAS_VENTANA = 7
MIN_EPISODIOS = 3
PRIORIDAD_MINIMA = 45  # umbral de P2 en telecorp_comun.prioridad.nivel()


def contar_episodios(estados: list[str], estado_previo: str | None = None) -> int:
    """Número de pasos NORMAL → no NORMAL en una serie ordenada por tiempo.

    `estado_previo` es la última lectura antes de la ventana; sin ella, una
    ventana que empieza degradada no cuenta ese primer episodio (no se sabe
    cuándo empezó).
    """
    episodios = 0
    anterior = estado_previo
    for estado in estados:
        if anterior == EstadoAntena.NORMAL and estado != EstadoAntena.NORMAL:
            episodios += 1
        anterior = estado
    return episodios


def id_recurrencia(antena_id: str, fecha_referencia) -> str:
    anio, semana, _ = fecha_referencia.astimezone(ZONA_LIMA).isocalendar()
    return f"REC-{antena_id}-{anio}-W{semana:02d}"


def _lecturas(antena_id: str, desde: str, hasta: str, cliente) -> tuple[str | None, list[str]]:
    tabla = nombres.tabla("metricas-antena")
    previa = cliente.query(
        TableName=tabla,
        KeyConditionExpression="antenaId = :a AND #ts < :desde",
        ExpressionAttributeNames={"#ts": "ts", "#e": "estado"},
        ExpressionAttributeValues={":a": {"S": antena_id}, ":desde": {"S": desde}},
        ProjectionExpression="#e",
        ScanIndexForward=False,
        Limit=1,
    ).get("Items", [])
    estado_previo = previa[0]["estado"]["S"] if previa and "estado" in previa[0] else None

    estados: list[str] = []
    for pagina in cliente.get_paginator("query").paginate(
        TableName=tabla,
        KeyConditionExpression="antenaId = :a AND #ts BETWEEN :desde AND :hasta",
        ExpressionAttributeNames={"#ts": "ts", "#e": "estado"},
        ExpressionAttributeValues={":a": {"S": antena_id}, ":desde": {"S": desde}, ":hasta": {"S": hasta}},
        ProjectionExpression="#ts, #e",
        ScanIndexForward=True,
    ):
        estados.extend(i["estado"]["S"] for i in pagina.get("Items", []) if "estado" in i)
    return estado_previo, estados


def handler(event, context=None, *, cliente=None):
    cliente = cliente or boto3.client("dynamodb")
    antena_id = event["antenaId"]
    zona_id = event.get("zonaId")
    referencia = parsear_fecha(event.get("fechaReferencia"))
    desde = iso(referencia - timedelta(days=DIAS_VENTANA))
    hasta = iso(referencia)

    estado_previo, estados = _lecturas(antena_id, desde, hasta, cliente)
    episodios = contar_episodios(estados, estado_previo)
    resultado = {"antenaId": antena_id, "episodios": episodios, "lecturas": len(estados), "creada": False}
    if episodios < MIN_EPISODIOS:
        return resultado

    incidencia_id = id_recurrencia(antena_id, referencia)
    zona = leer_zona(zona_id, cliente=cliente) if zona_id else {}
    usuarios = int(zona.get("usuariosEstimados") or 0)
    severidad = "ALTA" if episodios >= 2 * MIN_EPISODIOS else "MEDIA"
    valor = max(PRIORIDAD_MINIMA, prioridad.calcular(usuarios, zona.get("tipoZona") or TIPO_ZONA_POR_DEFECTO, severidad))
    nueva = registrar_inicial(
        {
            "incidenciaId": incidencia_id,
            "clase": "COLECTIVA",
            "origen": "RECURRENCIA",
            "zonaId": zona_id,
            "tipo": "INTERMITENCIA",
            "usuariosAfectados": usuarios,
            "antenasAfectadas": [antena_id],
            "severidadIA": severidad,
            "resumenIA": (
                f"Falla recurrente: la antena {antena_id} tuvo {episodios} episodios de degradación "
                f"entre {desde} y {hasta}."
            ),
            "episodios": episodios,
            "prioridad": valor,
            "nivelPrioridad": prioridad.nivel(valor),
        },
        ACTOR,
        cliente=cliente,
    )
    # Si ya existía (reintento), se completa la transición pendiente si hiciera falta.
    r = transiciones.mover(
        incidencia_id, Estado.EN_REVISION, ACTOR, f"{episodios} episodios de degradación en {DIAS_VENTANA} días",
        cliente=cliente,
    )
    logger.info("Recurrencia %s (nueva=%s): %s", incidencia_id, nueva, r)
    return {
        **resultado,
        "creada": nueva,
        "incidenciaId": incidencia_id,
        "estado": r.get("estadoFinal"),
        "nivelPrioridad": prioridad.nivel(valor),
    }
