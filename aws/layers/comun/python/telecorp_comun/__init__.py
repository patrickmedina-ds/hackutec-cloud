"""Lógica compartida de Red TeleCorp. Ver contratos/README.md."""

from . import antenas, eventos, nombres, prioridad
from .estados import Estado, TransicionInvalida, registrar_inicial, transicionar, validar_transicion

__all__ = [
    "Estado",
    "TransicionInvalida",
    "antenas",
    "eventos",
    "nombres",
    "prioridad",
    "registrar_inicial",
    "transicionar",
    "validar_transicion",
]
