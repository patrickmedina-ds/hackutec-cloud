"""Lectura de la configuración de federación desde SSM, con caché por contenedor."""

from __future__ import annotations

import os
import time

import boto3
from telecorp_comun import nombres

TTL_SEGUNDOS = int(os.environ.get("CONFIG_TTL_SEGUNDOS", "300"))

CLAVES = {
    "gcp_wif_config": "gcp/wif-config",
    "azure_tenant_id": "azure/tenant-id",
    "azure_client_id": "azure/client-id",
    "azure_eventgrid_endpoint": "azure/eventgrid-endpoint",
}


class ConfiguracionFaltante(RuntimeError):
    """Un parámetro SSM requerido no existe."""


def stage() -> str:
    return nombres.stage()


def nombre_tema() -> str:
    """Tema de Pub/Sub y de Event Grid: `telecorp-<stage>-eventos` (contratos/README.md)."""
    return nombres.recurso("eventos")


def nombre_parametro(clave: str) -> str:
    return f"/telecorp/{stage()}/{CLAVES[clave]}"


class Configuracion:
    """Lee los cuatro parámetros en una sola llamada `GetParameters` y los cachea."""

    def __init__(self, cliente_ssm=None, ttl: int = TTL_SEGUNDOS, reloj=time.monotonic):
        self._ssm = cliente_ssm
        self._ttl = ttl
        self._reloj = reloj
        self._valores: dict[str, str] = {}
        self._leido_en: float | None = None

    def _cliente(self):
        if self._ssm is None:
            self._ssm = boto3.client("ssm")
        return self._ssm

    def _recargar(self) -> None:
        nombres = {nombre_parametro(c): c for c in CLAVES}
        r = self._cliente().get_parameters(Names=list(nombres), WithDecryption=True)
        self._valores = {nombres[p["Name"]]: p["Value"] for p in r.get("Parameters", [])}
        self._leido_en = self._reloj()

    def obtener(self, clave: str) -> str:
        if self._leido_en is None or self._reloj() - self._leido_en > self._ttl:
            self._recargar()
        if clave not in self._valores:
            raise ConfiguracionFaltante(f"Falta el parámetro SSM {nombre_parametro(clave)}")
        return self._valores[clave]

    def invalidar(self) -> None:
        self._leido_en = None
