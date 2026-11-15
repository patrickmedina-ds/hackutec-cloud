"""Cliente de Azure OpenAI (Chat Completions REST) con salida JSON estricta.

- Autenticación sin llaves: token de Entra ID (scope cognitiveservices) obtenido con
  `telecorp_comun.entra` a partir del JWT de Cognito Identity (identidad de desarrollador).
- `response_format = json_schema` con `strict: true` (Structured Outputs), api-version GA.
- Configuración en SSM `/telecorp/<stage>/...` (una llamada `GetParameters`, TTL 5 min).
"""

from __future__ import annotations

import copy
import json
import os
import time

from telecorp_comun.entra import SCOPE_COGNITIVE, ProveedorTokenEntra, jwt_cognito

API_VERSION = "2024-10-21"  # GA; admite response_format json_schema strict
TEMPERATURA = 0.2
MAX_TOKENS = 1024
TTL_CONFIG_S = 300

CLAVES = {
    "tenant_id": "azure/tenant-id",
    "client_id": "azure/client-id",
    "endpoint": "azure/openai-endpoint",
    "deployment": "azure/openai-deployment",
    "identity_pool_id": "aws/identity-pool-id",
}

_cache: dict = {"http": None, "ssm": None, "config": {}, "leido_en": None, "tokens": None}


class ErrorIA(Exception):
    """Cualquier fallo al llamar a Azure OpenAI (red, Entra, API, formato)."""


def nombre_parametro(clave: str) -> str:
    return f"/telecorp/{os.environ.get('STAGE', 'dev')}/{CLAVES[clave]}"


def _http():
    if _cache["http"] is None:
        import requests

        _cache["http"] = requests.Session()
    return _cache["http"]


def config(clave: str) -> str:
    leido = _cache["leido_en"]
    if leido is None or time.monotonic() - leido > TTL_CONFIG_S:
        if _cache["ssm"] is None:
            import boto3

            _cache["ssm"] = boto3.client("ssm")
        nombres = {nombre_parametro(c): c for c in CLAVES}
        r = _cache["ssm"].get_parameters(Names=list(nombres), WithDecryption=True)
        _cache["config"] = {nombres[p["Name"]]: p["Value"] for p in r.get("Parameters", [])}
        _cache["leido_en"] = time.monotonic()
    if clave not in _cache["config"]:
        raise ErrorIA(f"Falta el parámetro SSM {nombre_parametro(clave)}")
    return _cache["config"][clave]


def _tokens() -> ProveedorTokenEntra:
    if _cache["tokens"] is None:
        _cache["tokens"] = ProveedorTokenEntra(
            _http(),
            tenant_id=lambda: config("tenant_id"),
            client_id=lambda: config("client_id"),
            origen_jwt=lambda: jwt_cognito(config("identity_pool_id")),
            scope=SCOPE_COGNITIVE,
            timeout=(3.05, 5),
        )
    return _cache["tokens"]


def esquema_estricto(esquema: dict) -> dict:
    """Structured Outputs exige additionalProperties=false y todos los campos en required."""
    e = copy.deepcopy(esquema)
    e["additionalProperties"] = False
    e["required"] = list(e.get("properties", {}))
    return e


def url_chat() -> str:
    endpoint = config("endpoint").rstrip("/")
    return f"{endpoint}/openai/deployments/{config('deployment')}/chat/completions?api-version={API_VERSION}"


def generar_json(sistema: str, prompt: str, esquema: dict, timeout: float) -> dict:
    """Pide al modelo un JSON que cumpla `esquema`. Lanza ErrorIA ante cualquier problema."""
    try:
        tokens = _tokens()
        r = _http().post(
            url_chat(),
            json={
                "messages": [
                    {"role": "system", "content": sistema},
                    {"role": "user", "content": prompt},
                ],
                "temperature": TEMPERATURA,
                "max_tokens": MAX_TOKENS,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "resultado", "strict": True, "schema": esquema_estricto(esquema)},
                },
            },
            headers={"Authorization": f"Bearer {tokens.token()}"},
            timeout=(3.05, max(1.0, timeout)),
        )
        tokens.revisar_respuesta(r.status_code)
        if r.status_code != 200:
            raise ErrorIA(f"Azure OpenAI respondió {r.status_code}: {r.text[:300]}")
        eleccion = r.json()["choices"][0]
        if eleccion.get("finish_reason") not in (None, "stop"):
            raise ErrorIA(f"Respuesta incompleta: {eleccion.get('finish_reason')}")
        mensaje = eleccion["message"]
        if mensaje.get("refusal"):
            raise ErrorIA("El modelo rechazó la solicitud")
        resultado = json.loads(mensaje["content"])
    except ErrorIA:
        raise
    except Exception as exc:  # red, timeout, Entra, Cognito, formato...
        raise ErrorIA(f"{type(exc).__name__}: {exc}") from exc
    if not isinstance(resultado, dict):
        raise ErrorIA("El modelo no devolvió un objeto JSON")
    return resultado
