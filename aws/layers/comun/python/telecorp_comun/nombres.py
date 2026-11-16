"""Nombres de recursos según contratos/README.md."""

import os

_TABLAS = {
    "incidencias": "TABLA_INCIDENCIAS",
    "historial-estados": "TABLA_HISTORIAL",
    "zona-ventana": "TABLA_ZONA_VENTANA",
    "zonas": "TABLA_ZONAS",
    "antenas": "TABLA_ANTENAS",
    "metricas-antena": "TABLA_METRICAS",
    "clientes": "TABLA_CLIENTES",
}


def stage() -> str:
    return os.environ.get("STAGE", "dev")


def recurso(sufijo: str) -> str:
    return f"telecorp-{stage()}-{sufijo}"


def tabla(nombre: str) -> str:
    """Nombre de una tabla: variable de entorno estándar o convención."""
    if nombre not in _TABLAS:
        raise KeyError(f"Tabla desconocida: {nombre}")
    return os.environ.get(_TABLAS[nombre]) or recurso(nombre)


def bus_eventos() -> str:
    return os.environ.get("BUS_EVENTOS") or recurso("eventos")
