"""Azure Functions (modelo de programación Python v2): replicar y api_estado."""
import json
import logging
import os

import azure.functions as func

from telecorp_azure import estado_api
from telecorp_azure.replicacion import cargar_cloudevent, procesar_evento

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

_repo = None
_notificador = None
_respaldo = None


def _obtener_repo():
    global _repo
    if _repo is None:
        from telecorp_azure.cosmos_repo import RepositorioCosmos
        _repo = RepositorioCosmos()
    return _repo


def _obtener_notificador():
    global _notificador
    if _notificador is None:
        from telecorp_azure.notificador_acs import NotificadorACS
        _notificador = NotificadorACS()
    return _notificador


def _obtener_respaldo():
    """Respaldo en Blob Storage; None si no hay RESPALDO_BLOB_ENDPOINT (p. ej. en local)."""
    global _respaldo
    if _respaldo is None and os.environ.get("RESPALDO_BLOB_ENDPOINT"):
        from telecorp_azure.respaldo import RespaldoBlob
        _respaldo = RespaldoBlob()
    return _respaldo


@app.function_name(name="replicar")
@app.event_grid_trigger(arg_name="evento")
def replicar(evento) -> None:
    """Recibe CloudEvents del tema de Event Grid. Si lanza, Event Grid reintenta y luego
    envía a dead letter."""
    cloudevent = cargar_cloudevent(evento)
    respaldo = _obtener_respaldo()
    if respaldo is not None:
        # Primero el respaldo del evento crudo; si falla, se lanza y Event Grid reintenta.
        creado = respaldo.guardar(cloudevent)
        logging.info("respaldo: %s", "creado" if creado else "ya existía")
    resultado = procesar_evento(cloudevent, _obtener_repo(), _obtener_notificador())
    logging.info("replicar: %s", resultado)


def _json(cuerpo, status=200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(cuerpo, ensure_ascii=False),
        status_code=status,
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.function_name(name="api_estado")
@app.route(route="zonas/{zonaId?}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def api_estado(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/zonas y GET /api/zonas/{zonaId}: solo lectura, sin PII."""
    zona_id = req.route_params.get("zonaId")
    repo = _obtener_repo()
    if not zona_id:
        return _json(estado_api.listar(repo))
    zona = estado_api.detalle(repo, zona_id)
    if zona is None:
        return _json({"mensaje": "Zona no encontrada"}, 404)
    return _json(zona)
