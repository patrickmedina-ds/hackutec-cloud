"""Respaldo de CloudEvents crudos en Azure Blob Storage (copia en otra nube).

Cada evento se guarda como `aaaa/mm/dd/<tipo>/<ce_id>.json` en el contenedor
`eventos-respaldo`. Es idempotente: la escritura usa `overwrite=False`, así que un
reintento o duplicado de Event Grid no pisa ni duplica el blob. Se autentica con
`DefaultAzureCredential` (identidad administrada de la Function); sin llaves.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

try:
    from azure.core.exceptions import ResourceExistsError
except ImportError:  # SDK no instalado (pruebas locales): mismo nombre, sin dependencia
    class ResourceExistsError(Exception):
        pass

CONTENEDOR_POR_DEFECTO = "eventos-respaldo"
_NO_SEGURO = re.compile(r"[^A-Za-z0-9._\-]")


def _segmento(valor: str) -> str:
    """Un solo segmento de ruta: sin '/', espacios ni caracteres raros."""
    return _NO_SEGURO.sub("_", str(valor)) or "_"


def _fecha(ce: dict) -> datetime:
    texto = ce.get("time")
    if isinstance(texto, str):
        try:
            f = datetime.fromisoformat(texto.replace("Z", "+00:00"))
            if f.tzinfo is None:
                f = f.replace(tzinfo=timezone.utc)
            return f.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def ruta_blob(ce: dict) -> str:
    """`aaaa/mm/dd/<tipo>/<ce_id>.json` (fecha UTC del campo `time`)."""
    if not ce.get("id") or not ce.get("type"):
        raise ValueError("CloudEvent sin id o type")
    f = _fecha(ce)
    return f"{f:%Y/%m/%d}/{_segmento(ce['type'])}/{_segmento(ce['id'])}.json"


class RespaldoBlob:
    def __init__(self, contenedor=None):
        """`contenedor`: ContainerClient (o simulado) con `upload_blob(name, data, overwrite=...)`."""
        if contenedor is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import ContainerClient

            contenedor = ContainerClient(
                account_url=os.environ["RESPALDO_BLOB_ENDPOINT"],
                container_name=os.environ.get("RESPALDO_CONTENEDOR", CONTENEDOR_POR_DEFECTO),
                credential=DefaultAzureCredential(),
            )
        self._contenedor = contenedor

    def guardar(self, ce: dict) -> bool:
        """Guarda el CloudEvent crudo. True si se creó; False si ya existía (idempotente)."""
        cuerpo = json.dumps(ce, ensure_ascii=False, default=str).encode("utf-8")
        try:
            self._contenedor.upload_blob(ruta_blob(ce), cuerpo, overwrite=False,
                                         content_type="application/json")
            return True
        except ResourceExistsError:
            return False
