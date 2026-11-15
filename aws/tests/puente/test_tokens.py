"""Caché de credenciales, tokens y configuración entre invocaciones."""

import json

import boto3
import pytest
from apoyo_puente import ConfigFalsa, Respuesta
from botocore.stub import Stubber
from configuracion import Configuracion, ConfiguracionFaltante, nombre_parametro
from destino_azure import PublicadorEventGrid, proveedor_token
from telecorp_comun.entra import MARGEN_RENOVACION, jwt_cognito
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
    tokens = proveedor_token(ConfigFalsa(), http, origen_jwt=lambda: emitidos.append(1) or "jwt", reloj=reloj)

    assert tokens.token() == "token-entra"
    reloj.t += 3599 - MARGEN_RENOVACION - 1
    assert tokens.token() == "token-entra"
    assert len(emitidos) == 1 and len(http.a("login.microsoftonline.com")) == 1

    reloj.t += 2  # ya dentro del margen de renovación
    tokens.token()
    assert len(emitidos) == 2 and len(http.a("login.microsoftonline.com")) == 2


def test_varias_publicaciones_usan_un_solo_token(http):
    tokens = proveedor_token(ConfigFalsa(), http, origen_jwt=lambda: "jwt")
    eg = PublicadorEventGrid(ConfigFalsa(), http, tokens)
    for _ in range(5):
        eg.publicar(CE)
    assert len(http.a("login.microsoftonline.com")) == 1
    assert len(http.a("eventgrid.azure.net")) == 5


def test_401_de_event_grid_descarta_el_token(http):
    tokens = proveedor_token(ConfigFalsa(), http, origen_jwt=lambda: "jwt")
    eg = PublicadorEventGrid(ConfigFalsa(), http, tokens)
    http.responder("eventgrid.azure.net", Respuesta(401, texto="Unauthorized"))
    with pytest.raises(RuntimeError):
        eg.publicar(CE)
    http.responder("eventgrid.azure.net", Respuesta(200))
    eg.publicar(CE)
    assert len(http.a("login.microsoftonline.com")) == 2


def test_jwt_cognito_usa_identidad_de_desarrollador_fija():
    cognito = boto3.client("cognito-identity", region_name="us-east-1",
                           aws_access_key_id="x", aws_secret_access_key="x")
    with Stubber(cognito) as stub:
        stub.add_response(
            "get_open_id_token_for_developer_identity",
            {"IdentityId": "us-east-1:abc", "Token": "eyJ.jwt.cognito"},
            {"IdentityPoolId": "us-east-1:pool", "Logins": {"telecorp.aws": "telecorp-test"},
             "TokenDuration": 300},
        )
        assert jwt_cognito("us-east-1:pool", cognito) == "eyJ.jwt.cognito"


def test_jwt_aws_lee_el_pool_de_la_variable_de_entorno(monkeypatch):
    import destino_azure

    vistos = []
    monkeypatch.setenv("COGNITO_IDENTITY_POOL_ID", "us-east-1:pool")
    monkeypatch.setattr(destino_azure, "jwt_cognito", lambda pool: vistos.append(pool) or "jwt")
    assert destino_azure.jwt_aws() == "jwt" and vistos == ["us-east-1:pool"]


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
