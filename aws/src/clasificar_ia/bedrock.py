"""Cliente de Amazon Bedrock Runtime (API `converse`) con tool use forzado.

Cada acción define un `toolSpec`; `toolChoice` obliga al modelo a invocarlo, de modo que
la salida llega como JSON estructurado (equivalente a responseSchema).
"""

from __future__ import annotations

import os

import boto3
from botocore.config import Config

# Perfil de inferencia por defecto; se puede cambiar a un modelo más nuevo disponible en la cuenta.
MODELO_DEFECTO = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
TEMPERATURA = 0.2
MAX_TOKENS = 1024
NOMBRE_HERRAMIENTA = "registrar_resultado"

_cache: dict = {"cliente": None}


class ErrorBedrock(Exception):
    """Cualquier fallo al llamar a Bedrock (red, API, formato)."""


def modelo() -> str:
    return os.environ.get("MODELO_IA") or MODELO_DEFECTO


def _cliente(timeout: float):
    if _cache["cliente"] is None:
        _cache["cliente"] = boto3.client(
            "bedrock-runtime",
            config=Config(connect_timeout=3, read_timeout=max(1, int(timeout)),
                          retries={"max_attempts": 1, "mode": "standard"}),
        )
    return _cache["cliente"]


def generar_json(sistema: str, prompt: str, esquema: dict, timeout: float) -> dict:
    """Llama a `converse` forzando la herramienta y devuelve su entrada (dict).

    Lanza ErrorBedrock ante cualquier problema; el llamador aplica el respaldo.
    """
    try:
        resp = _cliente(timeout).converse(
            modelId=modelo(),
            system=[{"text": sistema}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"temperature": TEMPERATURA, "maxTokens": MAX_TOKENS},
            toolConfig={
                "tools": [{"toolSpec": {
                    "name": NOMBRE_HERRAMIENTA,
                    "description": "Registra el resultado estructurado de la tarea.",
                    "inputSchema": {"json": esquema},
                }}],
                "toolChoice": {"tool": {"name": NOMBRE_HERRAMIENTA}},
            },
        )
        for bloque in resp["output"]["message"]["content"]:
            uso = bloque.get("toolUse")
            if uso and uso.get("name") == NOMBRE_HERRAMIENTA:
                resultado = uso["input"]
                break
        else:
            raise ErrorBedrock("El modelo no invocó la herramienta")
    except ErrorBedrock:
        raise
    except Exception as exc:  # red, timeout, throttling, acceso, formato...
        raise ErrorBedrock(f"{type(exc).__name__}: {exc}") from exc
    if not isinstance(resultado, dict):
        raise ErrorBedrock("El modelo no devolvió un objeto JSON")
    return resultado
