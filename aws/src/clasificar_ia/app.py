"""Lambda `clasificar-ia`: IA con Gemini (Vertex AI) y respaldo por reglas locales.

Contrato en contratos/integraciones.md sección 4. Nunca lanza excepciones a la
Step Function: ante cualquier fallo responde con reglas y `fuente = "reglas"`.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as TiempoAgotado

import prompts
import reglas
import vertex

log = logging.getLogger()
log.setLevel(logging.INFO)

LIMITE_VERTEX_S = 8.0  # presupuesto total para Vertex; pasado esto, reglas

_MAX_DESCRIPCION = 1000
_MAX_DESCRIPCIONES = 20
_MAX_ANTENAS = 30


# ---------- Normalización de la salida del modelo ----------

def _texto(valor, maximo: int, defecto: str = "") -> str:
    if not isinstance(valor, str):
        return defecto
    limpio = " ".join(valor.split())
    if len(limpio) > maximo:
        limpio = limpio[: maximo - 1].rstrip() + "…"
    return limpio or defecto


def _enum(valor, validos, defecto=None):
    if isinstance(valor, str):
        v = re.sub(r"[\s\-]+", "_", valor.strip().upper())
        v = reglas.normalizar_texto(v).upper()
        if v in validos:
            return v
        if v == "CRITICO":
            return "CRITICA"
    return defecto


def _confianza(valor) -> float:
    try:
        if isinstance(valor, bool):
            raise ValueError
        x = float(valor)
    except (TypeError, ValueError):
        return 0.5
    if x != x:  # NaN
        return 0.5
    return round(min(1.0, max(0.0, x)), 2)


def _normalizar_clasificar(crudo: dict, entrada: dict) -> dict:
    tipo = _enum(crudo.get("tipo"), reglas.TIPOS)
    severidad = _enum(crudo.get("severidad"), reglas.SEVERIDADES)
    resumen = _texto(crudo.get("resumen"), 200)
    if tipo is None or severidad is None or not resumen:
        raise ValueError("Salida de CLASIFICAR incompleta o con enums inválidos")
    return {"tipo": tipo, "severidad": severidad, "resumen": resumen,
            "confianza": _confianza(crudo.get("confianza"))}


def _normalizar_resumir(crudo: dict, entrada: dict) -> dict:
    severidad = _enum(crudo.get("severidad"), reglas.SEVERIDADES)
    resumen = _texto(crudo.get("resumen"), 400)
    causa = _texto(crudo.get("causaProbable"), 300)
    if severidad is None or not resumen or not causa:
        raise ValueError("Salida de RESUMIR_COLECTIVO incompleta o inválida")
    return {"resumen": resumen, "severidad": severidad, "causaProbable": causa}


def _normalizar_aviso(crudo: dict, entrada: dict) -> dict:
    asunto = _texto(crudo.get("asunto"), 100)
    mensaje = _texto(crudo.get("mensaje"), 1000)
    if not asunto or not mensaje:
        raise ValueError("Salida de REDACTAR_AVISO incompleta")
    if str(entrada.get("canal") or "SMS").upper() == "SMS":
        mensaje = reglas.acortar_sms(mensaje)
    return {"asunto": asunto, "mensaje": mensaje}


# ---------- Entrada saneada ----------

def _sanear(entrada: dict) -> dict:
    e = dict(entrada)
    if "descripcion" in e:
        e["descripcion"] = _texto(e.get("descripcion"), _MAX_DESCRIPCION)
    if "descripciones" in e:
        lista = e.get("descripciones") if isinstance(e.get("descripciones"), list) else []
        e["descripciones"] = [_texto(d, 300) for d in lista[:_MAX_DESCRIPCIONES] if d]
    if "antenas" in e:
        lista = e.get("antenas") if isinstance(e.get("antenas"), list) else []
        e["antenas"] = [
            {"antenaId": _texto(a.get("antenaId"), 60), "estado": _texto(a.get("estado"), 20)}
            for a in lista[:_MAX_ANTENAS] if isinstance(a, dict)
        ]
    return e


_ACCIONES = {
    "CLASIFICAR": (_normalizar_clasificar, reglas.clasificar),
    "RESUMIR_COLECTIVO": (_normalizar_resumir, reglas.resumir_colectivo),
    "REDACTAR_AVISO": (_normalizar_aviso, reglas.redactar_aviso),
}


def _llamar_con_limite(accion: str, entrada: dict) -> dict:
    """Ejecuta la llamada a Vertex con un tope duro de LIMITE_VERTEX_S segundos."""
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        futuro = pool.submit(
            vertex.generar_json, prompts.SISTEMA, prompts.construir(accion, entrada),
            prompts.ESQUEMAS[accion], LIMITE_VERTEX_S,
        )
        try:
            return futuro.result(timeout=LIMITE_VERTEX_S)
        except TiempoAgotado as exc:
            raise vertex.ErrorVertex(f"Vertex superó {LIMITE_VERTEX_S}s") from exc
    finally:
        pool.shutdown(wait=False)  # no esperar al hilo si quedó colgado


def procesar(evento: dict) -> dict:
    accion = str(evento.get("accion") or "").upper()
    if accion not in _ACCIONES:
        return {"error": f"Acción no soportada: {accion or '(vacía)'}", "fuente": "reglas"}
    normalizar, respaldo = _ACCIONES[accion]
    entrada = _sanear(evento)

    # Estados que no notifican: no hace falta gastar una llamada al modelo.
    if accion == "REDACTAR_AVISO" and str(entrada.get("estado") or "").upper() not in reglas.ESTADOS_NOTIFICABLES:
        return {**respaldo(entrada), "fuente": "reglas"}

    try:
        resultado = normalizar(_llamar_con_limite(accion, entrada), entrada)
        resultado["fuente"] = "gemini"
    except Exception as exc:  # noqa: BLE001 - el respaldo debe cubrir TODO
        log.warning("Respaldo por reglas en %s: %s", accion, exc)
        resultado = {**respaldo(entrada), "fuente": "reglas"}
    return resultado


def handler(evento, contexto=None):
    try:
        return procesar(evento if isinstance(evento, dict) else {})
    except Exception:  # noqa: BLE001 - jamás romper la Step Function
        log.exception("Fallo inesperado; intentando reglas locales")
        try:
            accion = str(evento.get("accion") or "").upper()
            return {**_ACCIONES[accion][1](evento), "fuente": "reglas"}
        except Exception:  # noqa: BLE001
            return {"error": "fallo interno", "fuente": "reglas"}
