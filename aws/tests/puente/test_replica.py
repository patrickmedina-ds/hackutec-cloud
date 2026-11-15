"""Envío a Azure y comportamiento del handler."""

import json

import app
import boto3
import pytest
import requests
from apoyo_puente import EVENTOS, ConfigFalsa, Respuesta
from destino_azure import PublicadorEventGrid, ProveedorTokenEntra
from moto import mock_aws


def _publicadores(http, config=None):
    config = config or ConfigFalsa()
    tokens = ProveedorTokenEntra(config, http, origen_jwt=lambda: "jwt-aws")
    return {"azure": PublicadorEventGrid(config, http, tokens)}


def test_publica_en_azure(http):
    resultado = app.replicar(EVENTOS["Antena.CambioEstado"], _publicadores(http), ["azure"])
    assert resultado["azure"]["ok"] is True

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


def test_si_falla_azure_se_reporta(http):
    http.responder("eventgrid.azure.net", requests.ConnectionError("sin red"))
    resultado = app.replicar(EVENTOS["Zona.UmbralSuperado"], _publicadores(http), ["azure"])
    assert resultado["azure"]["ok"] is False and "ConnectionError" in resultado["azure"]["error"]


def test_si_falla_entra_no_se_publica(http):
    http.responder("login.microsoftonline.com", Respuesta(400, texto="AADSTS70021: No matching federated identity"))
    resultado = app.replicar(EVENTOS["Zona.UmbralSuperado"], _publicadores(http), ["azure"])
    assert "AADSTS70021" in resultado["azure"]["error"]
    assert http.a("eventgrid.azure.net") == []


def test_falta_configuracion(http):
    config = ConfigFalsa()
    del config.valores["azure_eventgrid_endpoint"]
    resultado = app.replicar(EVENTOS["Zona.UmbralSuperado"], _publicadores(http, config), ["azure"])
    assert resultado["azure"]["ok"] is False and "ConfiguracionFaltante" in resultado["azure"]["error"]


@pytest.fixture
def handler_con_falsos(http, monkeypatch):
    monkeypatch.setattr(app, "_estado", _publicadores(http))
    return http


def test_handler_lanza_error_si_el_destino_falla(handler_con_falsos):
    handler_con_falsos.responder("eventgrid.azure.net", Respuesta(500, texto="error"))
    with pytest.raises(app.ErrorReplica, match="azure"):
        app.handler(EVENTOS["Incidencia.CambioEstado"], None)


def test_handler_ok(handler_con_falsos):
    r = app.handler(EVENTOS["Incidencia.CambioEstado"], None)
    assert r["id"] == "evt-inc-1"
    assert list(r["destinos"]) == ["azure"] and r["destinos"]["azure"]["ok"]


def test_destinos_por_defecto_solo_azure(monkeypatch):
    monkeypatch.delenv("DESTINOS", raising=False)
    assert app._destinos_activos() == ["azure"]


def test_handler_lee_configuracion_real_de_ssm(http, monkeypatch):
    """Con SSM simulado por moto: la Lambda arma todo a partir de los parámetros del contrato."""
    import configuracion
    import destino_azure

    monkeypatch.setattr(destino_azure, "jwt_iam_saliente", lambda: "jwt-aws")
    monkeypatch.setattr(app.requests, "Session", lambda: http)
    monkeypatch.setattr(app, "_estado", {})
    with mock_aws():
        ssm = boto3.client("ssm")
        for clave, valor in ConfigFalsa().valores.items():
            ssm.put_parameter(Name=configuracion.nombre_parametro(clave), Value=valor, Type="String")
        r = app.handler(EVENTOS["Antena.CambioEstado"], None)
    assert all(d["ok"] for d in r["destinos"].values())
