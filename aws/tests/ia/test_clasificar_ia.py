"""Pruebas de clasificar-ia: Azure OpenAI con HTTP simulado (sin red ni nubes).

Entra y Azure OpenAI se simulan con un `requests.Session` falso; Cognito Identity con
botocore Stubber; SSM con un cliente falso.
"""

import json
import os
import sys
import time
from pathlib import Path

import boto3
import pytest
import requests
from botocore.stub import Stubber

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "src" / "clasificar_ia"))
sys.path.insert(0, str(RAIZ / "layers" / "comun" / "python"))
for _m in ("app", "prompts", "reglas", "azure_openai"):
    sys.modules.pop(_m, None)
os.environ["STAGE"] = "test"

import app  # noqa: E402
import azure_openai  # noqa: E402
import reglas  # noqa: E402
from telecorp_comun import entra  # noqa: E402

ENDPOINT = "https://telecorp-test-openai.openai.azure.com/"
CONFIG = {
    "/telecorp/test/azure/tenant-id": "tenant-1",
    "/telecorp/test/azure/client-id": "cliente-1",
    "/telecorp/test/azure/openai-endpoint": ENDPOINT,
    "/telecorp/test/azure/openai-deployment": "gpt-4.1-mini",
    "/telecorp/test/aws/identity-pool-id": "us-east-1:pool",
}


class Resp:
    def __init__(self, status=200, cuerpo=None, texto=""):
        self.status_code = status
        self._c = cuerpo if cuerpo is not None else {}
        self.text = texto or json.dumps(self._c)

    def json(self):
        return self._c


class Http:
    """Sesión falsa: Entra responde `entra`; Azure OpenAI responde con la cola `openai`."""

    def __init__(self):
        self.llamadas = []
        self.openai = []
        self.entra = Resp(200, {"access_token": "token-entra", "expires_in": 3599})

    def post(self, url, **kw):
        self.llamadas.append({"url": url, **kw})
        if "login.microsoftonline.com" in url:
            return self.entra
        if not self.openai:
            raise AssertionError("llamada inesperada a Azure OpenAI")
        r = self.openai.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def a_openai(self):
        return [c for c in self.llamadas if "openai.azure.com" in c["url"]]

    def a_entra(self):
        return [c for c in self.llamadas if "login.microsoftonline.com" in c["url"]]


class Ssm:
    def __init__(self, valores):
        self.valores = valores

    def get_parameters(self, Names, WithDecryption):
        return {"Parameters": [{"Name": n, "Value": self.valores[n]} for n in Names if n in self.valores]}


def chat(contenido, finish="stop"):
    texto = contenido if isinstance(contenido, str) else json.dumps(contenido)
    return Resp(200, {"choices": [{"index": 0, "finish_reason": finish,
                                   "message": {"role": "assistant", "content": texto}}]})


class IA:
    def __init__(self, http, ssm):
        self.http = http
        self.ssm = ssm

    def responder(self, *rs):
        self.http.openai.extend(rs)

    @property
    def llamadas(self):
        return [c["json"] for c in self.http.a_openai()]


@pytest.fixture
def ia(monkeypatch):
    http, ssm = Http(), Ssm(dict(CONFIG))
    for k, v in {"http": http, "ssm": ssm, "config": {}, "leido_en": None, "tokens": None}.items():
        monkeypatch.setitem(azure_openai._cache, k, v)
    monkeypatch.setattr(azure_openai, "jwt_cognito", lambda pool: "jwt-cognito")
    return IA(http, ssm)


def esquema_de(llamada):
    rf = llamada["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    return rf["json_schema"]["schema"]


# ---------- Vía feliz (Azure OpenAI simulado) ----------

def test_clasificar_feliz(ia):
    ia.responder(chat({"tipo": "SIN_INTERNET", "severidad": "ALTA", "resumen": "Sin internet en clínica", "confianza": 0.86}))
    r = app.handler({"accion": "CLASIFICAR", "incidenciaId": "i1", "tipo": "LENTITUD",
                     "descripcion": "no hay internet en la clínica", "zonaId": "LIM-MIR-01", "tipoZona": "MEDICA"})
    assert r == {"tipo": "SIN_INTERNET", "severidad": "ALTA", "resumen": "Sin internet en clínica",
                 "confianza": 0.86, "fuente": "azure-openai"}
    (llamada_http,) = ia.http.a_openai()
    assert llamada_http["url"] == (
        "https://telecorp-test-openai.openai.azure.com/openai/deployments/gpt-4.1-mini/chat/completions"
        "?api-version=2024-10-21")
    assert llamada_http["headers"]["Authorization"] == "Bearer token-entra"
    (entra_http,) = ia.http.a_entra()
    assert entra_http["url"] == "https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token"
    assert entra_http["data"]["scope"] == "https://cognitiveservices.azure.com/.default"
    assert entra_http["data"]["client_assertion"] == "jwt-cognito"
    assert entra_http["data"]["client_id"] == "cliente-1"
    llamada = ia.llamadas[0]
    assert llamada["temperature"] <= 0.3
    esquema = esquema_de(llamada)
    assert esquema["additionalProperties"] is False
    assert set(esquema["required"]) == set(esquema["properties"]) == {"tipo", "severidad", "resumen", "confianza"}
    assert esquema["properties"]["tipo"]["enum"] == list(reglas.TIPOS)
    assert esquema["properties"]["severidad"]["enum"] == list(reglas.SEVERIDADES)
    assert llamada["messages"][0]["role"] == "system"
    assert "no hay internet en la clínica" in llamada["messages"][1]["content"]


def test_resumir_colectivo_feliz(ia):
    ia.responder(chat({"resumen": "Caída de antena en Miraflores", "severidad": "CRITICA",
                       "causaProbable": "Antena ANT-1 caída"}))
    r = app.handler({"accion": "RESUMIR_COLECTIVO", "incidenteColectivoId": "COL-1", "zonaId": "LIM-MIR-01",
                     "tipoZona": "MEDICA", "descripciones": ["sin señal", "no navega"],
                     "antenas": [{"antenaId": "ANT-1", "estado": "CAIDA"}]})
    assert r == {"resumen": "Caída de antena en Miraflores", "severidad": "CRITICA",
                 "causaProbable": "Antena ANT-1 caída", "fuente": "azure-openai"}
    esquema = esquema_de(ia.llamadas[0])
    assert set(esquema["properties"]) == {"resumen", "severidad", "causaProbable"}
    assert esquema["properties"]["severidad"]["enum"] == list(reglas.SEVERIDADES)


def test_redactar_aviso_feliz(ia):
    ia.responder(chat({"asunto": "Red TeleCorp: incidencia identificada en tu zona",
                       "mensaje": "Identificamos una incidencia en Miraflores y un equipo la está atendiendo."}))
    r = app.handler({"accion": "REDACTAR_AVISO", "estado": "ATENDIDA", "zonaNombre": "Miraflores", "canal": "EMAIL"})
    assert r["fuente"] == "azure-openai"
    assert r["asunto"].startswith("Red TeleCorp")
    assert "atendiendo" in r["mensaje"]
    assert set(esquema_de(ia.llamadas[0])["properties"]) == {"asunto", "mensaje"}


# ---------- Tokens y configuración ----------

def test_token_de_entra_se_reutiliza_entre_invocaciones(ia):
    ia.responder(chat({"asunto": "Red TeleCorp: aviso", "mensaje": "hola"}),
                 chat({"asunto": "Red TeleCorp: aviso", "mensaje": "hola"}))
    for _ in range(2):
        r = app.handler({"accion": "REDACTAR_AVISO", "estado": "ATENDIDA", "canal": "EMAIL"})
        assert r["fuente"] == "azure-openai"
    assert len(ia.http.a_entra()) == 1


def test_401_descarta_el_token_y_usa_reglas(ia):
    ia.responder(Resp(401, texto="Unauthorized"), chat({"asunto": "Red TeleCorp: aviso", "mensaje": "hola"}))
    r1 = app.handler({"accion": "REDACTAR_AVISO", "estado": "ATENDIDA", "canal": "EMAIL"})
    r2 = app.handler({"accion": "REDACTAR_AVISO", "estado": "ATENDIDA", "canal": "EMAIL"})
    assert r1["fuente"] == "reglas" and r2["fuente"] == "azure-openai"
    assert len(ia.http.a_entra()) == 2


def test_entra_rechaza_la_federacion_usa_reglas(ia):
    ia.http.entra = Resp(400, texto="AADSTS70021: No matching federated identity record")
    r = app.handler({"accion": "CLASIFICAR", "descripcion": "no hay internet"})
    assert r["fuente"] == "reglas" and ia.http.a_openai() == []


def test_falta_configuracion_usa_reglas(ia):
    del ia.ssm.valores["/telecorp/test/azure/openai-endpoint"]
    r = app.handler({"accion": "CLASIFICAR", "descripcion": "no hay internet"})
    assert r["fuente"] == "reglas" and ia.http.a_openai() == []


def test_jwt_cognito_con_stubber():
    cognito = boto3.client("cognito-identity", region_name="us-east-1",
                           aws_access_key_id="x", aws_secret_access_key="x")
    with Stubber(cognito) as stub:
        stub.add_response(
            "get_open_id_token_for_developer_identity",
            {"IdentityId": "us-east-1:id", "Token": "jwt"},
            {"IdentityPoolId": "us-east-1:pool", "Logins": {"telecorp.aws": "telecorp-test"}, "TokenDuration": 300},
        )
        assert entra.jwt_cognito("us-east-1:pool", cognito) == "jwt"


# ---------- Respaldo ----------

FALLAS = [
    ("throttling", lambda ia: ia.responder(Resp(429, texto="Too Many Requests"))),
    ("acceso", lambda ia: ia.responder(Resp(403, texto="PermissionDenied"))),
    ("error_500", lambda ia: ia.responder(Resp(500, texto="boom"))),
    ("timeout_red", lambda ia: ia.responder(requests.ReadTimeout("tarda"))),
    ("no_json", lambda ia: ia.responder(chat("esto no es json"))),
    ("truncado", lambda ia: ia.responder(chat({"tipo": "SIN_INTERNET"}, finish="length"))),
    ("filtro", lambda ia: ia.responder(chat({"tipo": "SIN_INTERNET"}, finish="content_filter"))),
    ("lista", lambda ia: ia.responder(chat([1, 2, 3]))),
    ("enum_invalido", lambda ia: ia.responder(chat(
        {"tipo": "INVENTADO", "severidad": "ALTA", "resumen": "x", "confianza": 1}))),
    ("incompleto", lambda ia: ia.responder(chat({"tipo": "SIN_INTERNET"}))),
    ("sin_choices", lambda ia: ia.responder(Resp(200, {"choices": []}))),
]


@pytest.mark.parametrize("preparar", [f[1] for f in FALLAS], ids=[f[0] for f in FALLAS])
def test_clasificar_respaldo_ante_fallas(ia, preparar):
    preparar(ia)
    r = app.handler({"accion": "CLASIFICAR", "tipo": "OTRO", "descripcion": "Sin internet desde ayer", "tipoZona": "RESIDENCIAL"})
    assert r["fuente"] == "reglas"
    assert r["tipo"] == "SIN_INTERNET"
    assert r["severidad"] in reglas.SEVERIDADES
    assert 0 <= r["confianza"] <= 1


def test_respaldo_en_las_tres_acciones_con_error(ia):
    ia.responder(Resp(500, texto="boom"), Resp(500, texto="boom"))
    c = app.handler({"accion": "RESUMIR_COLECTIVO", "zonaId": "Z", "descripciones": ["a"],
                     "antenas": [{"antenaId": "A1", "estado": "CAIDA"}, {"antenaId": "A2", "estado": "CAIDA"}]})
    assert c["fuente"] == "reglas" and c["severidad"] == "CRITICA" and "A1" in c["causaProbable"]
    a = app.handler({"accion": "REDACTAR_AVISO", "estado": "SOLUCIONADA", "zonaNombre": "Miraflores", "canal": "EMAIL"})
    assert a["fuente"] == "reglas" and "restablecido" in a["mensaje"] and a["asunto"]


def test_respaldo_por_tiempo_excedido(monkeypatch):
    def lenta(*args, **kwargs):
        time.sleep(2)
        return {"tipo": "OTRO", "severidad": "BAJA", "resumen": "x", "confianza": 1}

    monkeypatch.setattr(azure_openai, "generar_json", lenta)
    monkeypatch.setattr(app, "LIMITE_IA_S", 0.2)
    inicio = time.monotonic()
    r = app.handler({"accion": "CLASIFICAR", "descripcion": "no hay internet"})
    assert time.monotonic() - inicio < 1.5
    assert r["fuente"] == "reglas" and r["tipo"] == "SIN_INTERNET"


def test_nunca_lanza_excepcion(monkeypatch):
    def explota(*a, **k):
        raise RuntimeError("fallo inesperado")

    monkeypatch.setattr(azure_openai, "generar_json", explota)
    assert app.handler({"accion": "CLASIFICAR"})["fuente"] == "reglas"
    assert app.handler(None)["fuente"] == "reglas"
    assert app.handler({"accion": "NO_EXISTE"})["fuente"] == "reglas"
    assert app.handler({"accion": "RESUMIR_COLECTIVO", "descripciones": None, "antenas": "x"})["fuente"] == "reglas"
    monkeypatch.setattr(app, "procesar", explota)
    assert app.handler({"accion": "CLASIFICAR", "descripcion": "hospital"})["severidad"] == "CRITICA"


# ---------- SMS ----------

def test_sms_del_modelo_se_recorta_a_160(ia):
    largo = "Identificamos una incidencia en tu zona. " * 10
    ia.responder(chat({"asunto": "Red TeleCorp: aviso", "mensaje": largo}))
    r = app.handler({"accion": "REDACTAR_AVISO", "estado": "ATENDIDA", "canal": "SMS"})
    assert r["fuente"] == "azure-openai"
    assert 0 < len(r["mensaje"]) <= 160


@pytest.mark.parametrize("estado", ["EN_REVISION", "ATENDIDA", "SOLUCIONADA"])
def test_sms_de_plantilla_cabe_en_160_incluso_con_zona_larga(estado):
    r = reglas.redactar_aviso({"estado": estado, "canal": "SMS", "zonaNombre": "Urbanización " + "Muy Larga " * 30})
    assert 0 < len(r["mensaje"]) <= 160


def test_estados_sin_aviso_no_llaman_al_modelo(ia):
    for estado in ("REGISTRADA", "CERRADA"):
        r = app.handler({"accion": "REDACTAR_AVISO", "estado": estado, "canal": "SMS"})
        assert r["mensaje"] == "" and r["fuente"] == "reglas"
    assert ia.http.llamadas == []


def test_plantillas_respetan_el_sentido_de_eventos_md():
    en_revision = reglas.redactar_aviso({"estado": "EN_REVISION", "canal": "EMAIL"})["mensaje"]
    atendida = reglas.redactar_aviso({"estado": "ATENDIDA", "canal": "EMAIL"})["mensaje"]
    solucionada = reglas.redactar_aviso({"estado": "SOLUCIONADA", "canal": "EMAIL"})["mensaje"]
    assert "evaluando" in en_revision
    assert "atendiendo" in atendida
    assert "restablecido" in solucionada


# ---------- Normalización ----------

def test_normaliza_enums_longitudes_y_confianza(ia):
    ia.responder(chat({"tipo": " sin-internet ", "severidad": "crítica", "resumen": "r " * 300, "confianza": 7}))
    r = app.handler({"accion": "CLASIFICAR", "descripcion": "x"})
    assert r["fuente"] == "azure-openai"
    assert r["tipo"] == "SIN_INTERNET"
    assert r["severidad"] == "CRITICA"
    assert len(r["resumen"]) <= 200
    assert r["confianza"] == 1.0


@pytest.mark.parametrize("bruto,esperado", [(-3, 0.0), (0.456, 0.46), ("0.8", 0.8), ("abc", 0.5), (None, 0.5), (True, 0.5)])
def test_confianza_entre_0_y_1(bruto, esperado):
    assert app._confianza(bruto) == esperado


def test_resumir_con_severidad_invalida_usa_reglas(ia):
    ia.responder(chat({"resumen": "x", "severidad": "ENORME", "causaProbable": "y"}))
    r = app.handler({"accion": "RESUMIR_COLECTIVO", "zonaId": "Z", "descripciones": ["a"], "antenas": []})
    assert r["fuente"] == "reglas"


# ---------- Reglas de palabras clave ----------

@pytest.mark.parametrize("texto", ["Hospital sin servicio", "la CLÍNICA no tiene señal", "es una emergencia"])
def test_palabras_criticas_suben_a_critica(texto):
    assert reglas.clasificar({"descripcion": texto, "tipo": "LENTITUD"})["severidad"] == "CRITICA"


@pytest.mark.parametrize("texto,tipo", [
    ("Estoy sin señal desde la mañana", "SIN_INTERNET"),
    ("No hay internet", "SIN_INTERNET"),
    ("no puedo llamar a nadie", "SIN_LLAMADAS"),
    ("no llegan SMS de mi banco", "SIN_SMS"),
    ("el internet es muy lento", "LENTITUD"),
    ("la conexión es intermitente", "INTERMITENCIA"),
])
def test_palabras_clave_marcan_el_tipo(texto, tipo):
    assert reglas.clasificar({"descripcion": texto, "tipo": "OTRO"})["tipo"] == tipo


def test_sin_pistas_respeta_tipo_declarado_o_usa_otro():
    assert reglas.clasificar({"descripcion": "algo raro", "tipo": "SIN_SMS"})["tipo"] == "SIN_SMS"
    assert reglas.clasificar({"descripcion": "algo raro", "tipo": "XYZ"})["tipo"] == "OTRO"


def test_zona_medica_y_agravantes_suben_severidad():
    base = reglas.clasificar({"descripcion": "lento", "tipoZona": "RESIDENCIAL"})["severidad"]
    medica = reglas.clasificar({"descripcion": "lento", "tipoZona": "MEDICA"})["severidad"]
    assert reglas.SEVERIDADES.index(medica) > reglas.SEVERIDADES.index(base)
    assert reglas.clasificar({"descripcion": "sin internet en toda la zona"})["severidad"] == "ALTA"
