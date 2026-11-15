"""Lógica pura de `replicar`: sin dependencias del runtime de Functions ni del SDK de Azure.

El repositorio (Cosmos DB) y el notificador (ACS) se inyectan, así se prueban con simulados.

Interfaz esperada del repositorio:
    reclamar_evento(ce_id) -> bool          # True si es la primera vez (idempotencia)
    liberar_evento(ce_id) -> None           # deshace la marca si el procesamiento falló
    obtener_incidencia(incidencia_id) -> dict | None
    guardar_incidencia(doc) -> None
    listar_incidencias_zona(zona_id) -> list[dict]
    modificar_zona(zona_id, fn) -> dict     # fn(doc) -> doc; con reintento por etag

Interfaz del notificador:
    enviar(canal, destino, asunto, mensaje, incidencia_id=None, estado=None) -> None
    sms_habilitado -> bool                            # hay número remitente configurado
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

log = logging.getLogger("telecorp.replicar")

ESTADOS_ABIERTOS = ("REGISTRADA", "EN_REVISION", "ATENDIDA")
ESTADOS_PAGINA = ("normal", "en_evaluacion", "incidencia_identificada", "restablecido")
LIMITE_SMS = 160

TIPO_INCIDENCIA = "Incidencia.CambioEstado"
TIPO_ANTENA = "Antena.CambioEstado"
TIPO_NOTIFICACION = "Notificacion.Solicitada"


class Repositorio(Protocol):
    def reclamar_evento(self, ce_id: str) -> bool: ...
    def liberar_evento(self, ce_id: str) -> None: ...
    def obtener_incidencia(self, incidencia_id: str) -> dict | None: ...
    def guardar_incidencia(self, doc: dict) -> None: ...
    def listar_incidencias_zona(self, zona_id: str) -> list[dict]: ...
    def modificar_zona(self, zona_id: str, fn: Callable[[dict], dict]) -> dict: ...


class Notificador(Protocol):
    sms_habilitado: bool

    def enviar(self, canal: str, destino: str, asunto: str | None, mensaje: str,
               incidencia_id: str | None = None, estado: str | None = None) -> None: ...


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enmascarar(destino: str) -> str:
    """Oculta el destino (PII) para los registros."""
    if not destino:
        return ""
    if "@" in destino:
        usuario, _, dominio = destino.partition("@")
        return f"{usuario[:1]}***@{dominio}"
    return f"***{destino[-3:]}"


# --------------------------------------------------------------------------- agregado

def calcular_agregado(incidencias: list[dict]) -> dict:
    """Agregado de zona a partir de todas las incidencias de la zona.

    Devuelve: abiertas (por estado), resueltas (SOLUCIONADA sin cerrar), colectivoActivo,
    incidenteColectivoId y prioridadMax (mayor prioridad numérica entre las abiertas).
    """
    abiertas = {e: 0 for e in ESTADOS_ABIERTOS}
    resueltas = 0
    colectivos: list[str] = []
    prioridad_max = None
    for inc in incidencias:
        estado = inc.get("estado")
        if estado in abiertas:
            abiertas[estado] += 1
            colectivo = inc.get("incidenteColectivoId")
            if colectivo and colectivo not in colectivos:
                colectivos.append(colectivo)
            p = inc.get("prioridad")
            if isinstance(p, (int, float)) and (prioridad_max is None or p > prioridad_max):
                prioridad_max = p
        elif estado == "SOLUCIONADA":
            resueltas += 1
    colectivos.sort()
    return {
        "abiertas": abiertas,
        "totalAbiertas": sum(abiertas.values()),
        "resueltas": resueltas,
        "colectivoActivo": bool(colectivos),
        "incidenteColectivoId": colectivos[0] if colectivos else None,
        "prioridadMax": prioridad_max,
    }


def antenas_degradadas(antenas: dict) -> list[str]:
    return sorted(a for a, info in antenas.items() if info.get("estado") != "NORMAL")


def estado_pagina(zona: dict) -> str:
    """normal | en_evaluacion | incidencia_identificada | restablecido."""
    abiertas = zona.get("abiertas") or {}
    if (
        abiertas.get("ATENDIDA", 0) > 0
        or zona.get("colectivoActivo")
        or zona.get("antenasDegradadas")
    ):
        return "incidencia_identificada"
    if abiertas.get("REGISTRADA", 0) > 0 or abiertas.get("EN_REVISION", 0) > 0:
        return "en_evaluacion"
    if zona.get("resueltas", 0) > 0:
        return "restablecido"
    return "normal"


def _completar_zona(doc: dict) -> dict:
    doc["antenasDegradadas"] = antenas_degradadas(doc.get("antenas") or {})
    doc["estado"] = estado_pagina(doc)
    doc["actualizadoEn"] = _ahora()
    return doc


# --------------------------------------------------------------------------- manejadores

def _doc_incidencia(data: dict, ce_id: str) -> dict:
    snap = data.get("snapshot") or {}
    return {
        "id": data["incidenciaId"],
        "incidenciaId": data["incidenciaId"],
        "zonaId": snap.get("zonaId"),
        "estado": data["estadoNuevo"],
        "estadoAnterior": data.get("estadoAnterior"),
        "actor": data.get("actor"),
        "ts": data["ts"],
        "clase": snap.get("clase"),
        "origen": snap.get("origen"),
        "tipo": snap.get("tipo"),
        "prioridad": snap.get("prioridad"),
        "nivelPrioridad": snap.get("nivelPrioridad"),
        "severidadIA": snap.get("severidadIA"),
        "incidenteColectivoId": snap.get("incidenteColectivoId"),
        "ultimoEventoId": ce_id,
    }


def manejar_cambio_incidencia(data: dict, ce_id: str, repo: Repositorio) -> dict:
    nuevo = _doc_incidencia(data, ce_id)
    previo = repo.obtener_incidencia(nuevo["incidenciaId"])
    # Eventos fuera de orden: no se retrocede el estado, pero el agregado se recalcula igual.
    if previo is None or previo.get("ts", "") <= nuevo["ts"]:
        repo.guardar_incidencia(nuevo)
        resultado = "incidencia_actualizada"
    else:
        resultado = "incidencia_obsoleta"
    zona_id = nuevo["zonaId"]
    if zona_id:
        agregado = calcular_agregado(repo.listar_incidencias_zona(zona_id))

        def aplicar(doc: dict) -> dict:
            doc.update(agregado)
            return _completar_zona(doc)

        repo.modificar_zona(zona_id, aplicar)
    return {"resultado": resultado, "zonaId": zona_id}


def manejar_cambio_antena(data: dict, ce_id: str, repo: Repositorio) -> dict:
    zona_id = data["zonaId"]

    def aplicar(doc: dict) -> dict:
        antenas = doc.setdefault("antenas", {})
        actual = antenas.get(data["antenaId"])
        # Descarta eventos más viejos que el último conocido de la misma antena.
        if actual is None or actual.get("ts", "") <= data["ts"]:
            antenas[data["antenaId"]] = {
                "estado": data["estadoNuevo"],
                "gbPorMinuto": data.get("gbPorMinuto"),
                "ts": data["ts"],
            }
        return _completar_zona(doc)

    repo.modificar_zona(zona_id, aplicar)
    return {"resultado": "antena_actualizada", "zonaId": zona_id}


def recortar_sms(mensaje: str) -> str:
    return mensaje if len(mensaje) <= LIMITE_SMS else mensaje[: LIMITE_SMS - 1] + "…"


def manejar_notificacion_solicitada(data: dict, ce_id: str, notificador: Notificador) -> dict:
    canal = data["canal"]
    inc = data.get("incidenciaId")
    if canal == "SMS":
        if not getattr(notificador, "sms_habilitado", False):
            log.warning("SMS omitido: no hay número remitente de ACS configurado "
                        "(incidencia=%s destino=%s); se da por resuelto",
                        inc, enmascarar(data["destino"]))
            return {"resultado": "sms_omitido_sin_remitente", "canal": canal}
        notificador.enviar("SMS", data["destino"], None, recortar_sms(data["mensaje"]),
                           incidencia_id=inc, estado=data.get("estado"))
    elif canal == "EMAIL":
        asunto = data.get("asunto") or "Red TeleCorp: actualización de tu incidencia"
        notificador.enviar("EMAIL", data["destino"], asunto, data["mensaje"],
                           incidencia_id=inc, estado=data.get("estado"))
    else:
        raise ValueError(f"Canal no soportado: {canal}")
    log.info("Aviso enviado por ACS: incidencia=%s canal=%s destino=%s",
             inc, canal, enmascarar(data["destino"]))
    return {"resultado": "aviso_enviado", "canal": canal}


# --------------------------------------------------------------------------- entrada

def procesar_evento(evento: dict, repo: Repositorio, notificador: Notificador) -> dict:
    """Procesa un CloudEvent. Idempotente por `id` (ce_id).

    Si el procesamiento falla, la marca se libera y se relanza la excepción para que
    Event Grid reintente (y al final envíe a dead letter).
    """
    ce_id = evento.get("id")
    tipo = evento.get("type")
    data = evento.get("data") or {}
    if not ce_id or not tipo:
        raise ValueError("CloudEvent sin id o type")

    if tipo not in (TIPO_INCIDENCIA, TIPO_ANTENA, TIPO_NOTIFICACION):
        log.info("Evento %s de tipo %s ignorado", ce_id, tipo)
        return {"resultado": "ignorado", "tipo": tipo}

    if not repo.reclamar_evento(ce_id):
        log.info("Evento %s duplicado; se omite", ce_id)
        return {"resultado": "duplicado", "ceId": ce_id}

    try:
        if tipo == TIPO_INCIDENCIA:
            return manejar_cambio_incidencia(data, ce_id, repo)
        if tipo == TIPO_ANTENA:
            return manejar_cambio_antena(data, ce_id, repo)
        return manejar_notificacion_solicitada(data, ce_id, notificador)
    except Exception:
        repo.liberar_evento(ce_id)
        raise


def cargar_cloudevent(entrada: Any) -> dict:
    """Normaliza lo que entrega el disparador (str, bytes, dict u objeto con get_json)."""
    import json

    if isinstance(entrada, (bytes, bytearray)):
        entrada = entrada.decode("utf-8")
    if isinstance(entrada, str):
        entrada = json.loads(entrada)
    if hasattr(entrada, "get_json"):
        entrada = entrada.get_json()
    if isinstance(entrada, list) and len(entrada) == 1:
        entrada = entrada[0]
    if not isinstance(entrada, dict):
        raise ValueError("Formato de evento no reconocido")
    # Tolera la forma EventGridEvent (eventType/data) además de CloudEvents.
    if "type" not in entrada and "eventType" in entrada:
        entrada = {**entrada, "type": entrada["eventType"]}
    return entrada
