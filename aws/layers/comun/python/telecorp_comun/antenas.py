"""Clasificación del tráfico de una antena (umbrales del enunciado)."""

from enum import StrEnum

UMBRAL_CAIDA_GB = 60
UMBRAL_SATURACION_GB = 129


class EstadoAntena(StrEnum):
    NORMAL = "NORMAL"
    CAIDA = "CAIDA"
    SATURADA = "SATURADA"
    INHABILITADA = "INHABILITADA"


def clasificar_trafico(gb_por_minuto: float) -> EstadoAntena:
    """0 GB/min = inhabilitada, < 60 = caída, > 129 = saturada, resto normal."""
    if gb_por_minuto < 0:
        raise ValueError("gbPorMinuto no puede ser negativo")
    if gb_por_minuto == 0:
        return EstadoAntena.INHABILITADA
    if gb_por_minuto < UMBRAL_CAIDA_GB:
        return EstadoAntena.CAIDA
    if gb_por_minuto > UMBRAL_SATURACION_GB:
        return EstadoAntena.SATURADA
    return EstadoAntena.NORMAL


def es_degradada(estado: str) -> bool:
    return estado != EstadoAntena.NORMAL
