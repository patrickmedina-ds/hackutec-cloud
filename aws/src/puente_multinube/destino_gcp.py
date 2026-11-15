"""Publicación en Pub/Sub autenticada con Workload Identity Federation (proveedor AWS).

`google-auth` firma una petición `GetCallerIdentity` con las credenciales del rol de
la Lambda (variables AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN),
la canjea en el STS de Google por un token federado y, si se pide, lo cambia por un
token de una cuenta de servicio. No se guarda ninguna llave.
"""

from __future__ import annotations

import json
import os

from configuracion import Configuracion, nombre_tema, stage

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
PUBSUB_URL = "https://pubsub.googleapis.com/v1/projects/{proyecto}/topics/{tema}:publish"
IMPERSONACION_URL = (
    "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{email}:generateAccessToken"
)
TIMEOUT = (3.05, 10)


class ErrorPubSub(RuntimeError):
    pass


def _crear_credenciales(info: dict):
    """Crea credenciales `external_account` de tipo AWS desde el JSON de configuración.

    Se usa el cargador específico (`google.auth.aws`) y se valida el tipo: así una
    configuración alterada en SSM no puede colar otro tipo de credencial.
    """
    from google.auth import aws  # import diferido: acelera el arranque y facilita las pruebas

    if info.get("type") != "external_account" or not str(info.get("subject_token_type", "")).startswith(
        "urn:ietf:params:aws:token-type:"
    ):
        raise ErrorPubSub("wif-config no es una configuración external_account de tipo AWS")
    return aws.Credentials.from_info(info, scopes=SCOPES)


def _crear_request_google():
    from google.auth.transport.requests import Request

    return Request()


class PublicadorPubSub:
    """Mantiene las credenciales entre invocaciones y solo refresca cuando expiran."""

    def __init__(self, config: Configuracion, http, fabrica_credenciales=None, fabrica_request=None):
        self._config = config
        self._http = http
        self._fabrica_credenciales = fabrica_credenciales or (lambda info: _crear_credenciales(info))
        self._fabrica_request = fabrica_request or (lambda: _crear_request_google())
        self._credenciales = None
        self._config_crudo: str | None = None
        self._proyecto: str | None = None
        self._request_google = None

    # --- credenciales -------------------------------------------------------------
    def _preparar(self) -> None:
        crudo = self._config.obtener("gcp_wif_config")
        if crudo == self._config_crudo and self._credenciales is not None:
            return
        info = json.loads(crudo)
        self._proyecto = os.environ.get("GCP_PROJECT_ID") or info.get("quota_project_id")
        if not self._proyecto:
            raise ErrorPubSub("wif-config no trae `quota_project_id` y no hay GCP_PROJECT_ID")
        if os.environ.get("GCP_IMPERSONAR", "false").lower() == "true":
            # Plan B: actuar como la cuenta de servicio del puente en vez de acceso directo.
            email = f"telecorp-{stage()}-puente@{self._proyecto}.iam.gserviceaccount.com"
            info["service_account_impersonation_url"] = IMPERSONACION_URL.format(email=email)
        self._credenciales = self._fabrica_credenciales(info)
        self._config_crudo = crudo

    def token(self) -> str:
        self._preparar()
        if not self._credenciales.valid:  # sin token o a < 3–4 min de expirar
            if self._request_google is None:
                self._request_google = self._fabrica_request()
            self._credenciales.refresh(self._request_google)
        return self._credenciales.token

    def _olvidar_token(self) -> None:
        # Fuerza a refrescar en el próximo intento (p. ej. tras un 401).
        self._credenciales = None
        self._config_crudo = None

    # --- publicación ----------------------------------------------------------------
    def publicar(self, mensaje: dict) -> str:
        """Publica un mensaje (`{"data", "attributes"}`) y devuelve su messageId."""
        token = self.token()
        url = PUBSUB_URL.format(proyecto=self._proyecto, tema=nombre_tema())
        r = self._http.post(
            url,
            json={"messages": [mensaje]},
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
        if r.status_code in (401, 403):
            self._olvidar_token()
        if r.status_code >= 300:
            raise ErrorPubSub(f"Pub/Sub respondió {r.status_code}: {r.text[:500]}")
        return r.json()["messageIds"][0]
