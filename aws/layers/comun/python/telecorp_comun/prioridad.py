"""Prioridad de una incidencia: usuarios afectados × tipo de zona × severidad."""

import math

PESO_ZONA = {
    "RESIDENCIAL": 1.0,
    "COMERCIAL": 1.2,
    "EDUCATIVA": 1.5,
    "INDUSTRIAL": 1.5,
    "MEDICA": 2.0,
    "CRITICA": 2.0,
}

BONO_SEVERIDAD = {"BAJA": 0, "MEDIA": 5, "ALTA": 10, "CRITICA": 20}

# Bono por antenas degradadas confirmadas en la zona (correlación).
BONO_ANTENA_DEGRADADA = 10


def calcular(
    usuarios_afectados: int,
    tipo_zona: str = "RESIDENCIAL",
    severidad: str | None = None,
    antena_degradada: bool = False,
) -> int:
    """Devuelve 0–100.

    base = 20·log10(1 + afectados): 1 usuario ≈ 6, 10 ≈ 21, 100 ≈ 40, 1000 ≈ 60.
    """
    base = 20 * math.log10(1 + max(usuarios_afectados, 0))
    extra = BONO_SEVERIDAD.get(severidad or "", 0)
    if antena_degradada:
        extra += BONO_ANTENA_DEGRADADA
    peso = PESO_ZONA.get(tipo_zona, 1.0)
    return min(100, round((base + extra) * peso))


def nivel(prioridad: int) -> str:
    if prioridad >= 70:
        return "P1"
    if prioridad >= 45:
        return "P2"
    if prioridad >= 25:
        return "P3"
    return "P4"
