"""Tarea `AplicarClasificacion` de la SFN `ciclo-vida`.

Entrada (la arma la SFN):
    {"incidenciaId": "...", "tipoZona": "MEDICA",
     "ia": {"tipo": "...", "severidad": "ALTA", "resumen": "...", "confianza": 0.86}}

Calcula la prioridad de un reporte individual (1 afectado) y lo pasa de
REGISTRADA a EN_REVISION guardando la clasificación. Si la incidencia ya avanzó
(p. ej. un colectivo la movió antes), solo guarda la clasificación: un
`CambioEstado` nunca genera otra transición igual.

Si el reporte pertenece a un colectivo que ya está ATENDIDA (incidencia
identificada, p. ej. por una antena caída), lo lleva también a ATENDIDA.
"""

from __future__ import annotations

import boto3
from telecorp_comun import prioridad
from telecorp_comun.estados import Estado, IncidenciaNoEncontrada, obtener

import transiciones
from util import TIPO_ZONA_POR_DEFECTO, logger, severidad_valida

ACTOR = "STEPFN:ciclo-vida"


def handler(event, context=None, *, cliente=None):
    cliente = cliente or boto3.client("dynamodb")
    incidencia_id = event["incidenciaId"]
    tipo_zona = event.get("tipoZona") or TIPO_ZONA_POR_DEFECTO
    ia = event.get("ia") or {}
    severidad = severidad_valida(ia.get("severidad"))

    valor = prioridad.calcular(1, tipo_zona, severidad)
    cambios = {
        "tipoIA": ia.get("tipo"),
        "severidadIA": severidad,
        "resumenIA": ia.get("resumen"),
        "prioridad": valor,
        "nivelPrioridad": prioridad.nivel(valor),
    }
    cambios = {k: v for k, v in cambios.items() if v is not None}

    resultado = transiciones.mover(
        incidencia_id, Estado.EN_REVISION, ACTOR, "Clasificada automáticamente", cambios, cliente=cliente
    )
    if resultado.get("motivo") == "NO_ENCONTRADA":
        logger.warning("Incidencia %s no encontrada", incidencia_id)
        return {**resultado, **cambios}

    # Correlación con un colectivo ya identificado.
    try:
        incidencia = obtener(incidencia_id, cliente=cliente)
        colectivo_id = incidencia.get("incidenteColectivoId")
        if colectivo_id and obtener(colectivo_id, cliente=cliente)["estado"] == Estado.ATENDIDA:
            extra = transiciones.mover(
                incidencia_id,
                Estado.ATENDIDA,
                ACTOR,
                f"Incidencia identificada en la zona ({colectivo_id})",
                cliente=cliente,
            )
            resultado["pasos"] = [*resultado.get("pasos", []), *extra.get("pasos", [])]
            resultado["estadoFinal"] = extra.get("estadoFinal", resultado.get("estadoFinal"))
    except IncidenciaNoEncontrada:
        pass

    logger.info("Clasificación aplicada: %s", {"incidenciaId": incidencia_id, **resultado})
    return {**resultado, **cambios}
