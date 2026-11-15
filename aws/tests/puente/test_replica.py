"""Envíos independientes a GCP y Azure, y comportamiento del handler."""

import json

import app
import boto3
import pytest
import requests
from apoyo_puente import EVENTOS, ConfigFalsa, CredencialesFalsas, Respuesta
from destino_azure import PublicadorEventGrid, ProveedorTokenEntra
from destino_gcp import PublicadorPubSub
from moto import mock_aws


def _publicadores(http, config=None):
    config = config or ConfigFalsa()
    credenciales = CredencialesFalsas()
    gcp = PublicadorPubSub(config, http, fabrica_credenciales=lambda info: credenciales,
                           fabrica_request=lambda: object())
    tokens = ProveedorTokenEntra(config, http, origen_jwt=lambda: "jwt-aws")
    azure = PublicadorEventGrid(config, http, tokens)
    return {"gcp": gcp, "azure": azure}


def test_publica_en_ambos_destinos(http):
    resultado = app.replicar(EVENTOS["Antena.CambioEstado"], _publicadores(http), ["gcp", "azure"])
    assert resultado["gcp"] == {"ok": True, "detalle": "m-1"}
    assert resultado["azure"]["ok"] is True

    (pubsub,) = http.a("pubsub.googleapis.com")
    assert pubsub["url"] == (
        "https://pubsub.googleapis.com/v1/projects/telecorp-proyecto/topics/telecorp-test-eventos:publish"
    )
    assert pubsub["headers"]["Authorization"] == "Bearer token-gcp-1"
    assert pubsub["json"]["messages"][0]["attributes"] == {
        "ce_type": "Antena.CambioEstado", "ce_id": "evt-ant-1", "zonaId": "LIM-MIR-01",
    }

    (eg,) = http.a("eventgrid.azure.net")
    assert eg["url"].endswith("/api/events?api-version=2018-01-01")
    assert eg["headers"]["Content-Type"].startswith("application/cloudevents-batch+json")
    assert eg["headers"]["Authorization"] == "Bearer token-entra"
    lote = json.loads(eg["data"])
    assert isinstance(lote, list) and lote[0]["specversion"] == "1.0" and lote[0]["id"] == "evt-ant-1"

    (entra,) = http.a("login.microsoftonline.com")
    assert entra["url"] == "https://login.microsoftonline.com/tenant-123/oauth2/v2.0/token"
    assert entra["data"]["client_assertion"] == "jwt-aws"
    assert entra["data"]["client_id"] == "cliente-456"
    assert entra["data"]["scope"] == "https://eventgrid.azure.net/.default"
    assert entra["data"]["grant_type"] == "client_credentials"


def test_si_falla_gcp_azure_igual_recibe_el_evento(http):
    http.responder("pubsub.googleapis.com", Respuesta(503, texto="no disponible"))
    resultado = app.replicar(EVENTOS["Zona.UmbralSuperado"], _publicadores(http), ["gcp", "azure"])
    assert resultado["gcp"]["ok"] is False and "503" in resultado["gcp"]["error"]
    assert resultado["azure"]["ok"] is True
    assert len(http.a("eventgrid.azure.net")) == 1


def test_si_falla_azure_gcp_igual_recibe_el_evento(http):
    http.responder("eventgrid.azure.net", requests.ConnectionError("sin red"))
    resultado = app.replicar(EVENTOS["Zona.UmbralSuperado"], _publicadores(http), ["gcp", "azure"])
    assert resultado["gcp"]["ok"] is True
    assert resultado["azure"]["ok"] is False and "ConnectionError" in resultado["azure"]["error"]


def test_si_falla_entra_gcp_igual_recibe_el_evento(http):
    http.responder("login.microsoftonline.com", Respuesta(400, texto="AADSTS70021: No matching federated identity"))
    resultado = app.replicar(EVENTOS["Zona.UmbralSuperado"], _publicadores(http), ["gcp", "azure"])
    assert resultado["gcp"]["ok"] is True
    assert "AADSTS70021" in resultado["azure"]["error"]
    assert http.a("eventgrid.azure.net") == []


def test_falta_configuracion_de_un_destino(http):
    config = ConfigFalsa()
    del config.valores["gcp_wif_config"]
    resultado = app.replicar(EVENTOS["Zona.UmbralSuperado"], _publicadores(http, config), ["gcp", "azure"])
    assert resultado["gcp"]["ok"] is False and "ConfiguracionFaltante" in resultado["gcp"]["error"]
    assert resultado["azure"]["ok"] is True


@pytest.fixture
def handler_con_falsos(http, monkeypatch):
    monkeypatch.setattr(app, "_estado", _publicadores(http))
    return http


def test_handler_lanza_error_si_un_destino_falla(handler_con_falsos):
    handler_con_falsos.responder("eventgrid.azure.net", Respuesta(500, texto="error"))
    with pytest.raises(app.ErrorReplica, match="azure"):
        app.handler(EVENTOS["Incidencia.CambioEstado"], None)
    # GCP sí se publicó antes de reportar el fallo.
    assert len(handler_con_falsos.a("pubsub.googleapis.com")) == 1


def test_handler_ok(handler_con_falsos):
    r = app.handler(EVENTOS["Incidencia.CambioEstado"], None)
    assert r["id"] == "evt-inc-1"
    assert all(d["ok"] for d in r["destinos"].values())


def test_handler_respeta_destinos_activos(handler_con_falsos, monkeypatch):
    monkeypatch.setenv("DESTINOS", "azure")
    handler_con_falsos.responder("pubsub.googleapis.com", Respuesta(500))
    r = app.handler(EVENTOS["Notificacion.Fallida"], None)
    assert list(r["destinos"]) == ["azure"]
    assert handler_con_falsos.a("pubsub.googleapis.com") == []


def test_handler_lee_configuracion_real_de_ssm(http, monkeypatch):
    """Con SSM simulado por moto: la Lambda arma todo a partir de los parámetros del contrato."""
    import configuracion
    import destino_azure
    import destino_gcp

    credenciales = CredencialesFalsas()
    monkeypatch.setattr(destino_gcp, "_crear_credenciales", lambda info: credenciales)
    monkeypatch.setattr(destino_gcp, "_crear_request_google", lambda: object())
    monkeypatch.setattr(destino_azure, "jwt_iam_saliente", lambda: "jwt-aws")
    monkeypatch.setattr(app.requests, "Session", lambda: http)
    monkeypatch.setattr(app, "_estado", {})
    with mock_aws():
        ssm = boto3.client("ssm")
        valores = ConfigFalsa().valores
        for clave, valor in valores.items():
            ssm.put_parameter(Name=configuracion.nombre_parametro(clave), Value=valor, Type="String")
        r = app.handler(EVENTOS["Antena.CambioEstado"], None)
    assert all(d["ok"] for d in r["destinos"].values())
    assert credenciales.refrescos == 1
