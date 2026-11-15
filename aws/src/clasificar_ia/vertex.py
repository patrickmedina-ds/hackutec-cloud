"""Cliente de Vertex AI (Gemini) por REST con Workload Identity Federation.

Las credenciales `external_account` se leen de SSM y se cachean entre invocaciones
(el objeto de credenciales renueva su token solo cuando caduca).
"""

from __future__ import annotations

import json
import os
import time

import boto3

MODELO = os.environ.get("MODELO_GEMINI", "gemini-2.5-flash")
TEMPERATURA = 0.2
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

_cache: dict = {"config": None, "credenciales": None}


class ErrorVertex(Exception):
    """Cualquier fallo al llamar a Vertex (red, HTTP, formato)."""


def _stage() -> str:
    return os.environ.get("STAGE", "dev")


def _ssm():
    return boto3.client("ssm")


def _leer_config() -> dict:
    """Lee (una vez) wif-config, project-id y region desde SSM."""
    if _cache["config"] is None:
        base = f"/telecorp/{_stage()}/gcp"
        resp = _ssm().get_parameters(
            Names=[f"{base}/wif-config", f"{base}/project-id", f"{base}/region"],
            WithDecryption=True,
        )
        valores = {p["Name"].rsplit("/", 1)[1]: p["Value"] for p in resp.get("Parameters", [])}
        faltan = {"wif-config", "project-id", "region"} - set(valores)
        if faltan:
            raise ErrorVertex(f"Faltan parámetros SSM: {sorted(faltan)}")
        _cache["config"] = {
            "wif": json.loads(valores["wif-config"]),
            "proyecto": valores["project-id"].strip(),
            "region": valores["region"].strip(),
        }
    return _cache["config"]


def _token(config: dict) -> str:
    # Importación diferida: arranque en frío más rápido y pruebas sin google-auth.
    import google.auth
    from google.auth.transport.requests import Request

    cred = _cache["credenciales"]
    if cred is None:
        cred, _ = google.auth.load_credentials_from_dict(config["wif"], scopes=SCOPES)
        _cache["credenciales"] = cred
    if not cred.valid:
        cred.refresh(Request())
    return cred.token


def _url(config: dict) -> str:
    region = config["region"]
    host = "aiplatform.googleapis.com" if region == "global" else f"{region}-aiplatform.googleapis.com"
    return (
        f"https://{host}/v1/projects/{config['proyecto']}/locations/{region}"
        f"/publishers/google/models/{MODELO}:generateContent"
    )


def generar_json(sistema: str, prompt: str, esquema: dict, timeout: float) -> dict:
    """Llama a generateContent y devuelve el JSON (dict) producido por el modelo.

    Lanza ErrorVertex ante cualquier problema; el llamador aplica el respaldo.
    """
    import requests

    inicio = time.monotonic()
    try:
        config = _leer_config()
        token = _token(config)
        cuerpo = {
            "systemInstruction": {"parts": [{"text": sistema}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": TEMPERATURA,
                "maxOutputTokens": 1024,
                "responseMimeType": "application/json",
                "responseSchema": esquema,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        restante = max(0.5, timeout - (time.monotonic() - inicio))
        resp = requests.post(
            _url(config),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=cuerpo,
            timeout=(min(3.0, restante), restante),
        )
        if resp.status_code != 200:
            raise ErrorVertex(f"HTTP {resp.status_code}: {resp.text[:200]}")
        texto = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        resultado = json.loads(texto)
    except ErrorVertex:
        raise
    except Exception as exc:  # red, timeout, JSON, credenciales, SSM...
        raise ErrorVertex(f"{type(exc).__name__}: {exc}") from exc
    if not isinstance(resultado, dict):
        raise ErrorVertex("El modelo no devolvió un objeto JSON")
    return resultado
