"""Publicación en Event Grid autenticada con una credencial de identidad federada de Entra ID.

Flujo (sin secretos guardados):
1. `sts:GetWebIdentityToken` (federación saliente de IAM) emite un JWT firmado por
   el emisor OIDC propio de la cuenta AWS, con `sub` = ARN del rol de la Lambda y
   `aud` = `api://AzureADTokenExchange`.
2. Ese JWT se presenta como `client_assertion` en el endpoint de token de Entra
   (`client_credentials`), que lo valida contra la credencial federada de la App
   Registration y devuelve un token con scope `https://eventgrid.azure.net/.default`.
3. El token se usa como `Bearer` en el endpoint del tema de Event Grid.

Plan B (`AZURE_ORIGEN_JWT=cognito`): el JWT lo emite Cognito Identity
(`GetOpenIdTokenForDeveloperIdentity`). Ver docs/federacion.md.
"""

from __future__ import annotations

import json
import os
import time

import boto3

from configuracion import Configuracion

AUDIENCIA_ENTRA = "api://AzureADTokenExchange"
SCOPE_EVENTGRID = "https://eventgrid.azure.net/.default"
TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
API_VERSION_EVENTGRID = "2018-01-01"
CONTENT_TYPE_LOTE = "application/cloudevents-batch+json; charset=utf-8"
DURACION_JWT = 300  # el JWT solo vive lo necesario para canjearlo
MARGEN_RENOVACION = 300  # renovar el token de Entra 5 min antes de que expire
TIMEOUT = (3.05, 10)


class ErrorEventGrid(RuntimeError):
    pass


class ErrorEntra(RuntimeError):
    pass


# --- origen del JWT -------------------------------------------------------------------
def jwt_iam_saliente(cliente_sts=None) -> str:
    """JWT de la federación saliente de IAM (requiere endpoint regional de STS)."""
    if cliente_sts is None:
        region = os.environ.get("AWS_REGION", "us-east-1")
        cliente_sts = boto3.client("sts", region_name=region,
                                   endpoint_url=f"https://sts.{region}.amazonaws.com")
    r = cliente_sts.get_web_identity_token(
        Audience=[AUDIENCIA_ENTRA],
        SigningAlgorithm="RS256",  # Entra valida RS256; ES384 no está documentado
        DurationSeconds=DURACION_JWT,
    )
    return r["WebIdentityToken"]


def jwt_cognito(cliente_cognito=None) -> str:
    """Plan B: JWT de Cognito Identity para una identidad de desarrollador fija."""
    cliente_cognito = cliente_cognito or boto3.client("cognito-identity")
    proveedor = os.environ["COGNITO_PROVEEDOR_DESARROLLADOR"]
    r = cliente_cognito.get_open_id_token_for_developer_identity(
        IdentityPoolId=os.environ["COGNITO_IDENTITY_POOL_ID"],
        Logins={proveedor: os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "puente-multinube")},
        TokenDuration=DURACION_JWT,
    )
    return r["Token"]


def _origen_jwt_por_defecto():
    return jwt_cognito if os.environ.get("AZURE_ORIGEN_JWT") == "cognito" else jwt_iam_saliente


# --- token de Entra -----------------------------------------------------------------------
class ProveedorTokenEntra:
    """Canjea el JWT de AWS por un token de Entra y lo cachea hasta poco antes de expirar."""

    def __init__(self, config: Configuracion, http, origen_jwt=None, reloj=time.time):
        self._config = config
        self._http = http
        self._origen_jwt = origen_jwt or _origen_jwt_por_defecto()
        self._reloj = reloj
        self._token: str | None = None
        self._expira_en: float = 0.0

    def token(self) -> str:
        if self._token and self._reloj() < self._expira_en - MARGEN_RENOVACION:
            return self._token
        tenant = self._config.obtener("azure_tenant_id")
        client_id = self._config.obtener("azure_client_id")
        r = self._http.post(
            TOKEN_URL.format(tenant=tenant),
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "scope": SCOPE_EVENTGRID,
                "client_assertion_type": ASSERTION_TYPE,
                "client_assertion": self._origen_jwt(),
            },
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            raise ErrorEntra(f"Entra respondió {r.status_code}: {r.text[:500]}")
        cuerpo = r.json()
        self._token = cuerpo["access_token"]
        self._expira_en = self._reloj() + int(cuerpo.get("expires_in", 3599))
        return self._token

    def olvidar(self) -> None:
        self._token = None
        self._expira_en = 0.0


# --- publicación -------------------------------------------------------------------------
class PublicadorEventGrid:
    def __init__(self, config: Configuracion, http, proveedor_token: ProveedorTokenEntra | None = None):
        self._config = config
        self._http = http
        self._tokens = proveedor_token or ProveedorTokenEntra(config, http)

    def publicar(self, ce: dict) -> None:
        """Publica un lote de un CloudEvent en el tema (esquema de entrada CloudEvents 1.0)."""
        endpoint = self._config.obtener("azure_eventgrid_endpoint")
        if "api-version=" not in endpoint:
            endpoint += ("&" if "?" in endpoint else "?") + f"api-version={API_VERSION_EVENTGRID}"
        r = self._http.post(
            endpoint,
            data=json.dumps([ce], ensure_ascii=False, default=str).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._tokens.token()}",
                "Content-Type": CONTENT_TYPE_LOTE,
            },
            timeout=TIMEOUT,
        )
        if r.status_code in (401, 403):
            self._tokens.olvidar()
        if r.status_code >= 300:
            raise ErrorEventGrid(f"Event Grid respondió {r.status_code}: {r.text[:500]}")
