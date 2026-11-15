"""Publicación en Event Grid autenticada con una credencial de identidad federada de Entra ID.

Flujo (sin secretos guardados), implementado en `telecorp_comun.entra`:
1. Cognito Identity (`GetOpenIdTokenForDeveloperIdentity`) emite un JWT para la
   identidad de desarrollador fija del stage (aud = IdentityPoolId, sub = IdentityId).
2. Ese JWT se canjea en Entra (client assertion) por un token con scope
   `https://eventgrid.azure.net/.default`.
3. El token se usa como `Bearer` en el endpoint del tema de Event Grid.
"""

from __future__ import annotations

import json
import os

from telecorp_comun.entra import SCOPE_EVENTGRID, ErrorEntra, ProveedorTokenEntra, jwt_cognito  # noqa: F401

from configuracion import Configuracion

API_VERSION_EVENTGRID = "2018-01-01"
CONTENT_TYPE_LOTE = "application/cloudevents-batch+json; charset=utf-8"
TIMEOUT = (3.05, 10)


class ErrorEventGrid(RuntimeError):
    pass


def jwt_aws() -> str:
    """JWT de Cognito Identity para el Identity Pool del stack (variable de entorno)."""
    return jwt_cognito(os.environ["COGNITO_IDENTITY_POOL_ID"])


def proveedor_token(config: Configuracion, http, origen_jwt=None, **kw) -> ProveedorTokenEntra:
    return ProveedorTokenEntra(
        http,
        tenant_id=lambda: config.obtener("azure_tenant_id"),
        client_id=lambda: config.obtener("azure_client_id"),
        origen_jwt=origen_jwt or (lambda: jwt_aws()),
        scope=SCOPE_EVENTGRID,
        **kw,
    )


class PublicadorEventGrid:
    def __init__(self, config: Configuracion, http, tokens: ProveedorTokenEntra | None = None):
        self._config = config
        self._http = http
        self._tokens = tokens or proveedor_token(config, http)

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
        self._tokens.revisar_respuesta(r.status_code)
        if r.status_code >= 300:
            raise ErrorEventGrid(f"Event Grid respondió {r.status_code}: {r.text[:500]}")
