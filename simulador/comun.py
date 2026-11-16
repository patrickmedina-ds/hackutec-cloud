"""Utilidades compartidas del simulador: datos de entrada, nombres de recursos, reloj y umbrales.

Los umbrales de antenas se importan de la capa común (`telecorp_comun`) para no duplicarlos.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

SIMULADOR_DIR = Path(__file__).resolve().parent
RAIZ_REPO = SIMULADOR_DIR.parent
LAYER_COMUN = RAIZ_REPO / "aws" / "layers" / "comun" / "python"
DATOS_EJEMPLO = SIMULADOR_DIR / "datos_ejemplo"

if LAYER_COMUN.is_dir() and str(LAYER_COMUN) not in sys.path:
    sys.path.insert(0, str(LAYER_COMUN))

from telecorp_comun.antenas import (  # noqa: E402  (la ruta de la capa se añade arriba)
    UMBRAL_CAIDA_GB,
    UMBRAL_SATURACION_GB,
    clasificar_trafico,
)

__all__ = [
    "DATOS_EJEMPLO",
    "ErrorSimulador",
    "REGION_POR_DEFECTO",
    "UMBRAL_CAIDA_GB",
    "UMBRAL_SATURACION_GB",
    "UMBRAL_USUARIOS_COLECTIVO",
    "VENTANA_COLECTIVO_HORAS",
    "antenas_de_zona",
    "ahora_utc",
    "cargar_antenas",
    "cargar_clientes",
    "clientes_activos",
    "cargar_zonas",
    "clasificar",
    "crear_cliente",
    "epoch_ms",
    "exigir_antena",
    "exigir_zona",
    "iso_z",
    "nombre_recurso",
    "ruta_datos",
    "stage_por_defecto",
]

REGION_POR_DEFECTO = "us-east-1"
# Enunciado: 10 o más usuarios distintos, misma zona, menos de 24 horas.
UMBRAL_USUARIOS_COLECTIVO = 10
VENTANA_COLECTIVO_HORAS = 24


class ErrorSimulador(Exception):
    """Error de uso o de datos. El CLI lo muestra y termina con código 2."""


def stage_por_defecto() -> str:
    """Usa la variable STAGE si existe; si no, `dev` (contratos/README.md)."""
    return os.environ.get("STAGE", "dev")


def ruta_datos(argumento: str | None) -> Path:
    """Elige la carpeta de datos: la indicada, `<repo>/datos` o `simulador/datos_ejemplo`."""
    if argumento:
        ruta = Path(argumento)
        if not (ruta / "zonas.json").exists() or not (ruta / "antenas.json").exists():
            raise ErrorSimulador(f"la carpeta {ruta} no tiene zonas.json y antenas.json")
        return ruta
    candidata = RAIZ_REPO / "datos"
    if (candidata / "zonas.json").exists() and (candidata / "antenas.json").exists():
        return candidata
    return DATOS_EJEMPLO


def _cargar_lista(ruta_dir: Path, nombre: str) -> list[dict]:
    archivo = ruta_dir / nombre
    try:
        with archivo.open(encoding="utf-8") as f:
            datos = json.load(f)
    except FileNotFoundError as e:
        raise ErrorSimulador(f"no existe {archivo}") from e
    except json.JSONDecodeError as e:
        raise ErrorSimulador(f"JSON inválido en {archivo}: {e}") from e
    if not isinstance(datos, list) or not datos:
        raise ErrorSimulador(f"{archivo} debe contener una lista no vacía")
    return datos


def cargar_zonas(ruta_dir: Path) -> list[dict]:
    """Lee `zonas.json`: lista de {zonaId, nombre, tipoZona, ciudad, lat, lon, usuariosEstimados}."""
    return _cargar_lista(ruta_dir, "zonas.json")


def cargar_antenas(ruta_dir: Path) -> list[dict]:
    """Lee `antenas.json`: lista de {antenaId, zonaId, lat, lon}."""
    return _cargar_lista(ruta_dir, "antenas.json")


def cargar_clientes(ruta_dir: Path) -> list[dict]:
    """Lee `clientes.json` (contratos): {numeroServicio, documento, telefono, email, zonaId, estadoContrato, ...}."""
    return _cargar_lista(ruta_dir, "clientes.json")


def clientes_activos(clientes: list[dict], zona_id: str | None = None) -> list[dict]:
    """Contratos ACTIVO (de una zona si se indica): solo ellos pueden reportar."""
    return [
        c for c in clientes
        if c.get("estadoContrato") == "ACTIVO" and (zona_id is None or c["zonaId"] == zona_id)
    ]


def antenas_de_zona(antenas: list[dict], zona_id: str) -> list[dict]:
    return [a for a in antenas if a["zonaId"] == zona_id]


def exigir_zona(zonas: list[dict], zona_id: str) -> dict:
    for zona in zonas:
        if zona["zonaId"] == zona_id:
            return zona
    disponibles = ", ".join(z["zonaId"] for z in zonas)
    raise ErrorSimulador(f"zona {zona_id!r} no existe; disponibles: {disponibles}")


def exigir_antena(antenas: list[dict], antena_id: str) -> dict:
    for antena in antenas:
        if antena["antenaId"] == antena_id:
            return antena
    raise ErrorSimulador(f"antena {antena_id!r} no existe en los datos")


def nombre_recurso(stage: str, sufijo: str) -> str:
    """Convención `telecorp-<stage>-<sufijo>` (contratos/README.md)."""
    return f"telecorp-{stage}-{sufijo}"


def ahora_utc() -> datetime:
    return datetime.now(UTC)


def iso_z(instante: datetime) -> str:
    """Formato ISO 8601 en UTC con sufijo Z, como en los contratos."""
    return instante.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_ms(instante: datetime) -> int:
    """Milisegundos desde epoch, equivalente a `$context.requestTimeEpoch`."""
    return int(instante.timestamp() * 1000)


def clasificar(gb_por_minuto: float) -> str:
    """Estado de la antena según `telecorp_comun.antenas.clasificar_trafico`."""
    return clasificar_trafico(gb_por_minuto).value


def crear_cliente(servicio: str, region: str, endpoint: str | None = None):
    """Crea un cliente boto3. Se importa aquí para que `--dry-run` no lo necesite."""
    import boto3

    kwargs = {"region_name": region}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.client(servicio, **kwargs)
