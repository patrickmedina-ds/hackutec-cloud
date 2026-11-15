"""Lógica pura de `api_estado`: proyecta documentos de zona a JSON público (sin PII)."""
from __future__ import annotations

from datetime import datetime, timezone

CAMPOS_PUBLICOS = (
    "zonaId", "nombre", "estado", "abiertas", "totalAbiertas", "colectivoActivo",
    "prioridadMax", "antenasDegradadas", "actualizadoEn",
)


def proyectar_zona(doc: dict) -> dict:
    zona = {k: doc.get(k) for k in CAMPOS_PUBLICOS}
    zona["estado"] = zona["estado"] or "normal"
    zona["abiertas"] = zona["abiertas"] or {"REGISTRADA": 0, "EN_REVISION": 0, "ATENDIDA": 0}
    zona["totalAbiertas"] = zona["totalAbiertas"] or 0
    zona["colectivoActivo"] = bool(zona["colectivoActivo"])
    zona["antenasDegradadas"] = zona["antenasDegradadas"] or []
    return zona


def listar(repo) -> dict:
    zonas = sorted((proyectar_zona(d) for d in repo.listar_zonas()), key=lambda z: z["zonaId"])
    return {"zonas": zonas, "generadoEn": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def detalle(repo, zona_id: str) -> dict | None:
    doc = repo.obtener_zona(zona_id)
    if doc is None:
        return None
    return {**proyectar_zona(doc),
            "generadoEn": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
