"""Respaldo local: reglas por palabras clave y plantillas fijas.

No depende de ninguna red ni servicio. Es lo que responde la Lambda cuando
Vertex AI falla, tarda más de 8 s o devuelve algo inválido.
"""

from __future__ import annotations

import unicodedata

TIPOS = ("SIN_INTERNET", "SIN_LLAMADAS", "SIN_SMS", "LENTITUD", "INTERMITENCIA", "OTRO")
SEVERIDADES = ("BAJA", "MEDIA", "ALTA", "CRITICA")
MAX_SMS = 160


def normalizar_texto(texto) -> str:
    """Minúsculas y sin tildes, para comparar palabras clave."""
    s = unicodedata.normalize("NFD", str(texto or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


# Orden = prioridad cuando coinciden varias (lo más específico primero).
_PALABRAS_TIPO: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SIN_LLAMADAS", ("sin llamadas", "no puedo llamar", "no entran llamadas", "no salen llamadas",
                      "no puedo hacer llamadas", "no recibo llamadas", "llamadas caidas",
                      "se cortan las llamadas")),
    ("SIN_SMS", ("sin sms", "no llegan sms", "no recibo sms", "no puedo enviar sms",
                 "no llegan mensajes", "mensajes de texto")),
    ("SIN_INTERNET", ("sin internet", "no hay internet", "no tengo internet", "no navega",
                      "sin datos moviles", "sin conexion", "no carga", "internet caido",
                      "se cayo el internet", "no hay conexion")),
    ("INTERMITENCIA", ("intermitente", "intermitencia", "se corta", "se cae y vuelve",
                       "va y viene", "a ratos", "a veces", "inestable")),
    ("LENTITUD", ("lento", "lentitud", "muy lenta", "demora", "tarda", "se traba", "baja velocidad")),
)
# "sin señal" y similares: cobertura general → SIN_INTERNET (lo más probable ante
# una falla de antena), salvo que el texto hable de llamadas/SMS.
_SIN_SENAL = ("sin senal", "no hay senal", "no tengo senal", "perdi la senal", "sin cobertura",
              "no hay cobertura")

_CRITICAS = ("hospital", "clinica", "emergencia", "ambulancia", "urgencias", "posta medica",
             "centro de salud", "bomberos", "comisaria", "policia")
_AGRAVANTES = ("todo el edificio", "toda la cuadra", "todo el barrio", "toda la zona", "varios vecinos",
               "todos los vecinos", "desde hace dias", "desde ayer", "hace horas",
               "no puedo trabajar", "teletrabajo", "negocio", "empresa", "urgente")
_ATENUANTES = ("de vez en cuando", "solo un momento", "ya volvio", "ya funciona", "consulta")

_BASE_TIPO = {
    "SIN_INTERNET": 1, "SIN_LLAMADAS": 2, "SIN_SMS": 1, "LENTITUD": 0, "INTERMITENCIA": 1, "OTRO": 0,
}
_BASE_ZONA = {"MEDICA": 1, "CRITICA": 1}


def _contiene(texto: str, palabras) -> bool:
    return any(p in texto for p in palabras)


def detectar_tipo(descripcion, tipo_declarado=None) -> str:
    """Tipo a partir del texto; si no hay pistas, respeta el tipo declarado por el usuario."""
    t = normalizar_texto(descripcion)
    for tipo, palabras in _PALABRAS_TIPO:
        if _contiene(t, palabras):
            return tipo
    if _contiene(t, _SIN_SENAL):
        return "SIN_INTERNET"
    return tipo_declarado if tipo_declarado in TIPOS else "OTRO"


def calcular_severidad(descripcion, tipo, tipo_zona=None) -> str:
    t = normalizar_texto(descripcion)
    if _contiene(t, _CRITICAS):
        return "CRITICA"
    nivel = _BASE_TIPO.get(tipo, 0) + _BASE_ZONA.get(str(tipo_zona or "").upper(), 0)
    if _contiene(t, _AGRAVANTES):
        nivel += 1
    if _contiene(t, _ATENUANTES):
        nivel -= 1
    nivel = max(0, min(nivel, 2))  # sin palabras críticas, las reglas no pasan de ALTA
    return SEVERIDADES[nivel]


def clasificar(entrada: dict) -> dict:
    descripcion = entrada.get("descripcion") or ""
    tipo = detectar_tipo(descripcion, entrada.get("tipo"))
    severidad = calcular_severidad(descripcion, tipo, entrada.get("tipoZona"))
    resumen = " ".join(str(descripcion).split())[:160] or f"Reporte de tipo {tipo.replace('_', ' ').lower()}."
    return {"tipo": tipo, "severidad": severidad, "resumen": resumen, "confianza": 0.5}


def resumir_colectivo(entrada: dict) -> dict:
    antenas = [a for a in (entrada.get("antenas") or []) if isinstance(a, dict)]
    caidas = [a for a in antenas if a.get("estado") == "CAIDA"]
    degradadas = [a for a in antenas if a.get("estado") not in (None, "NORMAL", "CAIDA")]
    descripciones = [str(d) for d in (entrada.get("descripciones") or [])]
    zona = entrada.get("zonaId") or "la zona"
    zona_sensible = str(entrada.get("tipoZona") or "").upper() in ("MEDICA", "CRITICA")

    texto = " ".join(descripciones)
    nivel = SEVERIDADES.index(calcular_severidad(texto, detectar_tipo(texto), entrada.get("tipoZona")))
    if caidas:
        nivel = max(nivel, 2)
    if len(caidas) >= 2 or (caidas and zona_sensible):
        nivel = 3

    if caidas:
        ids = ", ".join(str(a.get("antenaId")) for a in caidas[:5])
        causa = f"Posible caída de antena(s): {ids}."
    elif degradadas:
        causa = "Posible degradación de tráfico en antenas de la zona."
    else:
        causa = "Causa no determinada; sin antenas con falla registrada."

    resumen = (
        f"Varios usuarios reportan fallas en {zona} ({len(descripciones)} reportes). "
        f"Antenas caídas: {len(caidas)}; degradadas: {len(degradadas)}."
    )
    return {"resumen": resumen[:400], "severidad": SEVERIDADES[nivel], "causaProbable": causa[:300]}


# Plantillas por estado, alineadas con la tabla de contratos/eventos.md.
_PLANTILLAS = {
    "EN_REVISION": ("Red TeleCorp: recibimos tu reporte", "Recibimos tu reporte y lo estamos evaluando."),
    "ATENDIDA": (
        "Red TeleCorp: incidencia identificada en tu zona",
        "Identificamos una incidencia en tu zona y un equipo la está atendiendo.",
    ),
    "SOLUCIONADA": ("Red TeleCorp: servicio restablecido", "El servicio fue restablecido."),
}
ESTADOS_NOTIFICABLES = tuple(_PLANTILLAS)


def acortar_sms(texto: str, maximo: int = MAX_SMS) -> str:
    """Recorta a `maximo` caracteres, preferiblemente en fin de frase o de palabra."""
    texto = " ".join(str(texto).split())
    if len(texto) <= maximo:
        return texto
    corte = texto[:maximo]
    punto = max(corte.rfind(". "), corte.rfind("; "))
    if punto >= maximo // 2:
        return corte[: punto + 1]
    espacio = corte.rfind(" ")
    if espacio >= maximo // 2:
        corte = corte[:espacio]
    return corte.rstrip(" ,;:")


def redactar_aviso(entrada: dict) -> dict:
    estado = str(entrada.get("estado") or "").upper()
    canal = str(entrada.get("canal") or "SMS").upper()
    zona = " ".join(str(entrada.get("zonaNombre") or "").split())
    if estado not in _PLANTILLAS:
        return {"asunto": "", "mensaje": ""}  # REGISTRADA / CERRADA no notifican
    asunto, mensaje = _PLANTILLAS[estado]
    if zona and estado == "ATENDIDA":
        mensaje = f"Identificamos una incidencia en {zona} y un equipo la está atendiendo."
    elif zona and estado == "SOLUCIONADA":
        mensaje = f"El servicio fue restablecido en {zona}."
    if canal == "SMS":
        mensaje = acortar_sms("Red TeleCorp: " + mensaje)
    return {"asunto": asunto, "mensaje": mensaje}
