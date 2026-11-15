"""Tokens de Entra ID para Lambdas de AWS sin secretos guardados (Learner Lab).

Flujo:
1. Cognito Identity (`GetOpenIdTokenForDeveloperIdentity`) emite un JWT para una
   identidad de desarrollador fija (`{"telecorp.aws": "telecorp-<stage>"}`):
   iss=https://cognito-identity.amazonaws.com, aud=IdentityPoolId, sub=IdentityId.
2. Ese JWT se presenta como `client_assertion` (client_credentials) en Entra ID,
   que lo valida contra la credencial federada de la App Registration.
3. El token de Entra (scope pedido) se cachea hasta 5 min antes de expirar.

Lo usan `puente-multinube` (Event Grid) y `clasificar-ia` (Azure OpenAI).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
SCOPE_EVENTGRID = "https://eventgrid.azure.net/.default"
SCOPE_COGNITIVE = "https://cognitiveservices.azure.com/.default"
PROVEEDOR_DESARROLLADOR = "telecorp.aws"
DURACION_JWT = 300  # el JWT solo vive lo necesario para canjearlo
MARGEN_RENOVACION = 300  # renovar el token de Entra 5 min antes de que expire
TIMEOUT = (3.05, 10)


class ErrorEntra(RuntimeError):
    """Entra rechazó la aserción o no respondió."""


def proveedor_desarrollador() -> str:
    return os.environ.get("COGNITO_PROVEEDOR_DESARROLLADOR") or PROVEEDOR_DESARROLLADOR


def login_desarrollador() -> dict:
    """Login fijo: misma identidad (mismo IdentityId) para todas las Lambdas del stage."""
    stage = os.environ.get("STAGE", "dev")
    return {proveedor_desarrollador(): f"telecorp-{stage}"}


def jwt_cognito(identity_pool_id: str, cliente_cognito=None) -> str:
    """JWT OIDC de Cognito Identity para la identidad de desarrollador fija."""
    if cliente_cognito is None:
        import boto3

        cliente_cognito = boto3.client("cognito-identity")
    r = cliente_cognito.get_open_id_token_for_developer_identity(
        IdentityPoolId=identity_pool_id,
        Logins=login_desarrollador(),
        TokenDuration=DURACION_JWT,
    )
    return r["Token"]


class ProveedorTokenEntra:
    """Canjea el JWT de AWS por un token de Entra para `scope` y lo cachea.

    `tenant_id`, `client_id` y `origen_jwt` son funciones (se evalúan al renovar,
    así la configuración puede venir de SSM con su propio caché).
    """

    def __init__(
        self,
        http,
        tenant_id: Callable[[], str],
        client_id: Callable[[], str],
        origen_jwt: Callable[[], str],
        scope: str,
        reloj: Callable[[], float] = time.time,
        timeout=TIMEOUT,
    ):
        self._http = http
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._origen_jwt = origen_jwt
        self._scope = scope
        self._reloj = reloj
        self._timeout = timeout
        self._token: str | None = None
        self._expira_en = 0.0
        self._candado = threading.Lock()

    def token(self) -> str:
        with self._candado:
            if self._token and self._reloj() < self._expira_en - MARGEN_RENOVACION:
                return self._token
            r = self._http.post(
                TOKEN_URL.format(tenant=self._tenant_id()),
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id(),
                    "scope": self._scope,
                    "client_assertion_type": ASSERTION_TYPE,
                    "client_assertion": self._origen_jwt(),
                },
                timeout=self._timeout,
            )
            if r.status_code != 200:
                raise ErrorEntra(f"Entra respondió {r.status_code}: {r.text[:500]}")
            cuerpo = r.json()
            self._token = cuerpo["access_token"]
            self._expira_en = self._reloj() + int(cuerpo.get("expires_in", 3599))
            return self._token

    def olvidar(self) -> None:
        """Descarta el token (p. ej. tras un 401/403 del recurso)."""
        with self._candado:
            self._token = None
            self._expira_en = 0.0

    def revisar_respuesta(self, status_code: int) -> None:
        if status_code in (401, 403):
            self.olvidar()
