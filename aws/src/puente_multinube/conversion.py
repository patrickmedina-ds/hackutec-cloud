"""Conversión de eventos de EventBridge a CloudEvents 1.0 (contratos/integraciones.md §3)."""

from __future__ import annotations

import base64
import json

SPECVERSION = "1.0"
CONTENT_TYPE_JSON = "application/json"


class EventoInvalido(ValueError):
    """El evento recibido no tiene la forma de un evento de EventBridge."""


def _zona_id(detail: dict) -> str | None:
    """zonaId del detail: en la raíz o, para incidencias, dentro del snapshot."""
    zona = detail.get("zonaId")
    if not zona and isinstance(detail.get("snapshot"), dict):
        zona = detail["snapshot"].get("zonaId")
    return zona or None


def _subject(detail: dict) -> str | None:
    """`subject` = zonaId o incidenciaId.

    Se prefiere el zonaId de la raíz del detail (Zona.*, Antena.*); si no hay,
    el incidenciaId (Incidencia.*, Notificacion.*); como último recurso el
    zonaId del snapshot.
    """
    return detail.get("zonaId") or detail.get("incidenciaId") or _zona_id(detail)


def a_cloudevent(evento: dict) -> dict:
    """Convierte un evento de EventBridge en un CloudEvent 1.0 en modo estructurado."""
    faltantes = [c for c in ("id", "source", "detail-type", "time") if not evento.get(c)]
    if faltantes:
        raise EventoInvalido(f"Faltan campos en el evento: {faltantes}")
    detail = evento.get("detail") or {}
    if not isinstance(detail, dict):
        raise EventoInvalido("`detail` debe ser un objeto JSON")

    ce = {
        "specversion": SPECVERSION,
        "id": evento["id"],
        "source": f"telecorp/aws/{evento['source']}",
        "type": evento["detail-type"],
        "time": evento["time"],
        "datacontenttype": CONTENT_TYPE_JSON,
        "data": detail,
    }
    subject = _subject(detail)
    if subject:
        ce["subject"] = str(subject)
    return ce


def atributos_pubsub(ce: dict) -> dict[str, str]:
    """Atributos del mensaje de Pub/Sub: ce_type, ce_id y zonaId si existe."""
    atributos = {"ce_type": ce["type"], "ce_id": ce["id"]}
    zona = _zona_id(ce.get("data") or {})
    if zona:
        atributos["zonaId"] = str(zona)
    return atributos


def mensaje_pubsub(ce: dict) -> dict:
    """Mensaje para `topics.publish`: `data` = el CloudEvent completo en JSON (base64)."""
    cuerpo = json.dumps(ce, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    return {"data": base64.b64encode(cuerpo).decode("ascii"), "attributes": atributos_pubsub(ce)}
