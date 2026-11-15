"""Caché de credenciales, tokens y configuración entre invocaciones."""

import json

import boto3
import pytest
from apoyo_puente import ConfigFalsa, Respuesta
from botocore.stub import Stubber
from configuracion import Configuracion, ConfiguracionFaltante, nombre_parametro
from destino_azure import (
    AUDIENCIA_ENTRA,
    MARGEN_RENOVACION,
    PublicadorEventGrid,
    ProveedorTokenEntra,
    jwt_iam_saliente,
)
from moto import mock_aws

CE = {"specversion": "1.0", "id": "x", "source": "telecorp/aws/telecorp.zonas", "type": "Zona.UmbralSuperado",
      "time": "2026-11-15T14:03:00Z", "datacontenttype": "application/json", "data": {}}


class Reloj:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


# --- Azure / Entra ---------------------------------------------------------------------------
def test_token_entra_se_reutiliza_hasta_cerca_de_expirar(http):
    reloj = Reloj()
    emitidos = []
    tokens = ProveedorTokenEntra(ConfigFalsa(), http, origen_jwt=lambda: emitidos.append(1) or "jwt", reloj=reloj)

    assert tokens.token() == "token-entra"
    reloj.t += 3599 - MARGEN_RENOVACION - 1
    assert tokens.token() == "token-entra"
    assert len(emitidos) == 1 and len(http.a("login.microsoftonline.com")) == 1

    reloj.t += 2  # ya dentro del margen de renovación
    tokens.token()
    assert len(emitidos) == 2 and len(http.a("login.microsoftonline.com")) == 2


def test_varias_publicaciones_usan_un_solo_token(http):
    tokens = ProveedorTokenEntra(ConfigFalsa(), http, origen_jwt=lambda: "jwt")
    eg = PublicadorEventGrid(ConfigFalsa(), http, tokens)
    for _ in range(5):
        eg.publicar(CE)
    assert len(http.a("login.microsoftonline.com")) == 1
    assert len(http.a("eventgrid.azure.net")) == 5


def test_401_de_event_grid_descarta_el_token(http):
    tokens = ProveedorTokenEntra(ConfigFalsa(), http, origen_jwt=lambda: "jwt")
    eg = PublicadorEventGrid(ConfigFalsa(), http, tokens)
    http.responder("eventgrid.azure.net", Respuesta(401, texto="Unauthorized"))
    with pytest.raises(RuntimeError):
        eg.publicar(CE)
    http.responder("eventgrid.azure.net", Respuesta(200))
    eg.publicar(CE)
    assert len(http.a("login.microsoftonline.com")) == 2


def test_jwt_iam_saliente_pide_rs256_y_audiencia_de_entra():
    sts = boto3.client("sts", region_name="us-east-1")
    with Stubber(sts) as stub:
        stub.add_response(
            "get_web_identity_token",
            {"WebIdentityToken": "eyJ.jwt.firma", "Expiration": "2026-11-15T15:00:00Z"},
            {"Audience": [AUDIENCIA_ENTRA], "SigningAlgorithm": "RS256", "DurationSeconds": 300},
        )
        assert jwt_iam_saliente(sts) == "eyJ.jwt.firma"


# --- SSM ------------------------------------------------------------------------------------------
def test_configuracion_se_cachea_por_ttl():
    reloj = Reloj()
    with mock_aws():
        ssm = boto3.client("ssm")
        ssm.put_parameter(Name=nombre_parametro("azure_tenant_id"), Value="t-1", Type="String")
        config = Configuracion(ssm, ttl=300, reloj=reloj)
        llamadas = []
        ssm.meta.events.register("before-call.ssm.GetParameters", lambda **kw: llamadas.append(1))

        assert config.obtener("azure_tenant_id") == "t-1"
        with pytest.raises(ConfiguracionFaltante):
            config.obtener("azure_client_id")
        assert len(llamadas) == 1

        ssm.put_parameter(Name=nombre_parametro("azure_tenant_id"), Value="t-2", Type="String", Overwrite=True)
        reloj.t += 301
        assert config.obtener("azure_tenant_id") == "t-2"
        assert len(llamadas) == 2
