"""Tipos de evento del bus (contratos/eventos.md) y publicación directa."""

import json

import boto3

from . import nombres

CAMBIO_ESTADO_INCIDENCIA = "Incidencia.CambioEstado"
UMBRAL_ZONA = "Zona.UmbralSuperado"
CAMBIO_ESTADO_ANTENA = "Antena.CambioEstado"
NOTIFICACION_SOLICITADA = "Notificacion.Solicitada"

SOURCE = {
    CAMBIO_ESTADO_INCIDENCIA: "telecorp.incidencias",
    UMBRAL_ZONA: "telecorp.zonas",
    CAMBIO_ESTADO_ANTENA: "telecorp.antenas",
    NOTIFICACION_SOLICITADA: "telecorp.notificaciones",
}

USUARIOS_UMBRAL_COLECTIVO = 10
VENTANA_COLECTIVO_HORAS = 24


def publicar(detail_type: str, detail: dict, *, cliente=None) -> str:
    """Publica en el bus. Solo para eventos que no salen de un Pipe."""
    cliente = cliente or boto3.client("events")
    respuesta = cliente.put_events(
        Entries=[
            {
                "EventBusName": nombres.bus_eventos(),
                "Source": SOURCE[detail_type],
                "DetailType": detail_type,
                "Detail": json.dumps(detail, default=str),
            }
        ]
    )
    if respuesta.get("FailedEntryCount"):
        raise RuntimeError(f"PutEvents falló: {respuesta['Entries']}")
    return respuesta["Entries"][0]["EventId"]
