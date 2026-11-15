"""Lambda `telecorp-<stage>-puente-multinube`.

Recibe cada evento `telecorp.*` del bus, lo convierte a CloudEvents 1.0 y lo replica
en Event Grid (Azure). Si el envío falla, se lanza `ErrorReplica` para que la invocación
asíncrona se reintente (y acabe en la DLQ `telecorp-<stage>-puente-dlq`).

Credenciales, tokens y configuración viven en variables de módulo para reutilizarse
entre invocaciones del mismo contenedor.
"""

from __future__ import annotations

import json
import logging
import os

import requests

from configuracion import Configuracion
from conversion import a_cloudevent
from destino_azure import PublicadorEventGrid

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


class ErrorReplica(RuntimeError):
    """Al menos un destino falló; la invocación debe reintentarse."""


_estado: dict = {}


def _destinos_activos() -> list[str]:
    return [d.strip() for d in os.environ.get("DESTINOS", "azure").split(",") if d.strip()]


def _publicadores() -> dict:
    """Crea (una sola vez por contenedor) la sesión HTTP, la config y los publicadores."""
    if not _estado:
        http = requests.Session()
        config = Configuracion()
        _estado["azure"] = PublicadorEventGrid(config, http)
    return _estado


def _log(nivel: int, mensaje: str, **campos) -> None:
    logger.log(nivel, json.dumps({"mensaje": mensaje, **campos}, ensure_ascii=False, default=str))


def replicar(evento: dict, publicadores: dict, destinos: list[str]) -> dict:
    """Envía el evento a cada destino por destino. Devuelve el resultado por destino."""
    ce = a_cloudevent(evento)
    envios = {
        "azure": lambda: publicadores["azure"].publicar(ce),
    }
    resultado: dict[str, dict] = {}
    for destino in destinos:
        try:
            detalle = envios[destino]()
            resultado[destino] = {"ok": True, "detalle": detalle}
            _log(logging.INFO, "replicado", destino=destino, ce_id=ce["id"], ce_type=ce["type"])
        except Exception as e:  # noqa: BLE001 — un destino nunca debe tumbar al otro
            resultado[destino] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            _log(logging.ERROR, "fallo_replica", destino=destino, ce_id=ce["id"],
                 ce_type=ce["type"], error=resultado[destino]["error"])
    return resultado


def handler(evento, contexto):
    resultado = replicar(evento, _publicadores(), _destinos_activos())
    fallidos = [d for d, r in resultado.items() if not r["ok"]]
    if fallidos:
        raise ErrorReplica(f"Falló la réplica a {fallidos} para el evento {evento.get('id')}")
    return {"id": evento.get("id"), "destinos": resultado}
