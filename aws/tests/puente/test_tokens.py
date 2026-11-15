"""Caché de credenciales, tokens y configuración entre invocaciones."""

import json

import boto3
import pytest
from apoyo_puente import ConfigFalsa, CredencialesFalsas, Respuesta
from botocore.stub import Stubber
from configuracion import Configuracion, ConfiguracionFaltante, nombre_parametro
from destino_azure import (
    AUDIENCIA_ENTRA,
    MARGEN_RENOVACION,
    PublicadorEventGrid,
    ProveedorTokenEntra,
    jwt_iam_saliente,
)
from destino_gcp import PublicadorPubSub
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


# --- GCP ----------------------------------------------------------------------------------------
def test_credenciales_gcp_se_crean_y_refrescan_una_vez(http):
    creadas = []

    def fabrica(info):
        creadas.append(info)
        return CredencialesFalsas()

    pubsub = PublicadorPubSub(ConfigFalsa(), http, fabrica_credenciales=fabrica, fabrica_request=lambda: object())
    for _ in range(3):
        pubsub.publicar({"data": "e30=", "attributes": {}})
    assert len(creadas) == 1
    assert pubsub._credenciales.refrescos == 1
    assert {c["headers"]["Authorization"] for c in http.a("pubsub")} == {"Bearer token-gcp-1"}


def test_credenciales_gcp_se_refrescan_al_expirar(http):
    pubsub = PublicadorPubSub(ConfigFalsa(), http, fabrica_credenciales=lambda info: CredencialesFalsas(),
                              fabrica_request=lambda: object())
    pubsub.publicar({"data": "e30=", "attributes": {}})
    pubsub._credenciales.valid = False  # google-auth marca inválido el token próximo a expirar
    pubsub.publicar({"data": "e30=", "attributes": {}})
    assert pubsub._credenciales.refrescos == 2


def test_impersonacion_opcional_de_cuenta_de_servicio(http, monkeypatch):
    monkeypatch.setenv("GCP_IMPERSONAR", "true")
    recibido = {}
    pubsub = PublicadorPubSub(ConfigFalsa(), http,
                              fabrica_credenciales=lambda info: recibido.update(info) or CredencialesFalsas(),
                              fabrica_request=lambda: object())
    pubsub.publicar({"data": "e30=", "attributes": {}})
    assert recibido["service_account_impersonation_url"].endswith(
        "serviceAccounts/telecorp-test-puente@telecorp-proyecto.iam.gserviceaccount.com:generateAccessToken"
    )


def test_google_auth_acepta_la_configuracion_de_terraform(monkeypatch):
    """El JSON que genera gcp/terraform/federacion.tf produce credenciales aws de google-auth."""
    pytest.importorskip("google.auth")
    import destino_gcp
    from google.auth import aws

    info = {
        "universe_domain": "googleapis.com",
        "type": "external_account",
        "audience": "//iam.googleapis.com/projects/123/locations/global/workloadIdentityPools/telecorp-test-aws/providers/aws",
        "subject_token_type": "urn:ietf:params:aws:token-type:aws4_request",
        "token_url": "https://sts.googleapis.com/v1/token",
        "credential_source": {
            "environment_id": "aws1",
            "region_url": "http://169.254.169.254/latest/meta-data/placement/availability-zone",
            "url": "http://169.254.169.254/latest/meta-data/iam/security-credentials",
            "regional_cred_verification_url": "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15",
        },
        "quota_project_id": "telecorp-proyecto",
    }
    credenciales = destino_gcp._crear_credenciales(json.loads(json.dumps(info)))
    assert isinstance(credenciales, aws.Credentials)
    assert credenciales.quota_project_id == "telecorp-proyecto"
    assert not credenciales.valid


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
            config.obtener("gcp_wif_config")
        assert len(llamadas) == 1

        ssm.put_parameter(Name=nombre_parametro("azure_tenant_id"), Value="t-2", Type="String", Overwrite=True)
        reloj.t += 301
        assert config.obtener("azure_tenant_id") == "t-2"
        assert len(llamadas) == 2
