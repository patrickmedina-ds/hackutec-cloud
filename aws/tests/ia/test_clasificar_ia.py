"""Pruebas de clasificar-ia: sin red ni nubes reales (HTTP y credenciales simulados)."""

import json
import sys
import time
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "clasificar_ia"))

import app  # noqa: E402
import reglas  # noqa: E402
import vertex  # noqa: E402


class RespuestaFalsa:
    def __init__(self, status=200, cuerpo=None, texto=None):
        self.status_code = status
        self._cuerpo = cuerpo
        self.text = texto if texto is not None else json.dumps(cuerpo)

    def json(self):
        return self._cuerpo


def respuesta_gemini(objeto) -> RespuestaFalsa:
    texto = objeto if isinstance(objeto, str) else json.dumps(objeto)
    return RespuestaFalsa(200, {"candidates": [{"content": {"parts": [{"text": texto}]}}]})


@pytest.fixture
def http(monkeypatch):
    """Simula SSM/credenciales y captura las llamadas HTTP a Vertex."""
    monkeypatch.setattr(vertex, "_leer_config", lambda: {"wif": {}, "proyecto": "proy-demo", "region": "us-east1"})
    monkeypatch.setattr(vertex, "_token", lambda config: "token-falso")
    estado = {"llamadas": [], "respuesta": respuesta_gemini({})}

    def post(url, headers=None, json=None, timeout=None):
        estado["llamadas"].append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        r = estado["respuesta"]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(requests, "post", post)
    return estado


# ---------- Vía feliz (HTTP simulado) ----------

def test_clasificar_feliz(http):
    http["respuesta"] = respuesta_gemini(
        {"tipo": "SIN_INTERNET", "severidad": "ALTA", "resumen": "Sin internet en clínica", "confianza": 0.86}
    )
    r = app.handler({"accion": "CLASIFICAR", "incidenciaId": "i1", "tipo": "LENTITUD",
                     "descripcion": "no hay internet en la clínica", "zonaId": "LIM-MIR-01", "tipoZona": "MEDICA"})
    assert r == {"tipo": "SIN_INTERNET", "severidad": "ALTA", "resumen": "Sin internet en clínica",
                 "confianza": 0.86, "fuente": "gemini"}
    llamada = http["llamadas"][0]
    assert llamada["url"].startswith("https://us-east1-aiplatform.googleapis.com/v1/projects/proy-demo/")
    assert llamada["url"].endswith("/publishers/google/models/gemini-2.5-flash:generateContent")
    assert llamada["headers"]["Authorization"] == "Bearer token-falso"
    cfg = llamada["json"]["generationConfig"]
    assert cfg["responseMimeType"] == "application/json"
    assert cfg["temperature"] <= 0.3
    assert set(cfg["responseSchema"]["properties"]) == {"tipo", "severidad", "resumen", "confianza"}
    assert "no hay internet en la clínica" in llamada["json"]["contents"][0]["parts"][0]["text"]


def test_resumir_colectivo_feliz(http):
    http["respuesta"] = respuesta_gemini(
        {"resumen": "Caída de antena en Miraflores", "severidad": "CRITICA", "causaProbable": "Antena ANT-1 caída"}
    )
    r = app.handler({"accion": "RESUMIR_COLECTIVO", "incidenteColectivoId": "COL-1", "zonaId": "LIM-MIR-01",
                     "tipoZona": "MEDICA", "descripciones": ["sin señal", "no navega"],
                     "antenas": [{"antenaId": "ANT-1", "estado": "CAIDA"}]})
    assert r["fuente"] == "gemini"
    assert r["severidad"] == "CRITICA"
    assert r["causaProbable"] == "Antena ANT-1 caída"
    assert set(http["llamadas"][0]["json"]["generationConfig"]["responseSchema"]["properties"]) == {
        "resumen", "severidad", "causaProbable"}


def test_redactar_aviso_feliz(http):
    http["respuesta"] = respuesta_gemini(
        {"asunto": "Red TeleCorp: incidencia identificada en tu zona",
         "mensaje": "Identificamos una incidencia en Miraflores y un equipo la está atendiendo."}
    )
    r = app.handler({"accion": "REDACTAR_AVISO", "estado": "ATENDIDA", "zonaNombre": "Miraflores", "canal": "EMAIL"})
    assert r["fuente"] == "gemini"
    assert r["asunto"].startswith("Red TeleCorp")
    assert "atendiendo" in r["mensaje"]


# ---------- Respaldo ----------

@pytest.mark.parametrize("falla", [
    requests.exceptions.ReadTimeout("timeout"),
    requests.exceptions.ConnectionError("sin red"),
    RespuestaFalsa(500, texto="Internal error"),
    RespuestaFalsa(429, texto="cuota"),
    respuesta_gemini("esto no es json {"),
    respuesta_gemini("[1, 2, 3]"),
    RespuestaFalsa(200, {"candidates": []}),
    respuesta_gemini({"tipo": "INVENTADO", "severidad": "ALTA", "resumen": "x", "confianza": 1}),
    respuesta_gemini({"tipo": "SIN_INTERNET"}),
])
def test_clasificar_respaldo_ante_fallas(http, falla):
    http["respuesta"] = falla
    r = app.handler({"accion": "CLASIFICAR", "tipo": "OTRO", "descripcion": "Sin internet desde ayer", "tipoZona": "RESIDENCIAL"})
    assert r["fuente"] == "reglas"
    assert r["tipo"] == "SIN_INTERNET"
    assert r["severidad"] in reglas.SEVERIDADES
    assert 0 <= r["confianza"] <= 1


def test_respaldo_en_las_tres_acciones_con_500(http):
    http["respuesta"] = RespuestaFalsa(500, texto="boom")
    c = app.handler({"accion": "RESUMIR_COLECTIVO", "zonaId": "Z", "descripciones": ["a"],
                     "antenas": [{"antenaId": "A1", "estado": "CAIDA"}, {"antenaId": "A2", "estado": "CAIDA"}]})
    assert c["fuente"] == "reglas" and c["severidad"] == "CRITICA" and "A1" in c["causaProbable"]
    a = app.handler({"accion": "REDACTAR_AVISO", "estado": "SOLUCIONADA", "zonaNombre": "Miraflores", "canal": "EMAIL"})
    assert a["fuente"] == "reglas" and "restablecido" in a["mensaje"] and a["asunto"]


def test_respaldo_por_tiempo_excedido(monkeypatch):
    def lenta(*args, **kwargs):
        time.sleep(2)
        return {"tipo": "OTRO", "severidad": "BAJA", "resumen": "x", "confianza": 1}

    monkeypatch.setattr(vertex, "generar_json", lenta)
    monkeypatch.setattr(app, "LIMITE_VERTEX_S", 0.2)
    inicio = time.monotonic()
    r = app.handler({"accion": "CLASIFICAR", "descripcion": "no hay internet"})
    assert time.monotonic() - inicio < 1.5
    assert r["fuente"] == "reglas" and r["tipo"] == "SIN_INTERNET"


def test_nunca_lanza_excepcion(monkeypatch):
    def explota(*a, **k):
        raise RuntimeError("fallo inesperado")

    monkeypatch.setattr(vertex, "generar_json", explota)
    assert app.handler({"accion": "CLASIFICAR"})["fuente"] == "reglas"
    assert app.handler(None)["fuente"] == "reglas"
    assert app.handler({"accion": "NO_EXISTE"})["fuente"] == "reglas"
    assert app.handler({"accion": "RESUMIR_COLECTIVO", "descripciones": None, "antenas": "x"})["fuente"] == "reglas"
    monkeypatch.setattr(app, "procesar", explota)
    assert app.handler({"accion": "CLASIFICAR", "descripcion": "hospital"})["severidad"] == "CRITICA"


def test_ssm_ausente_activa_respaldo(monkeypatch):
    def sin_ssm():
        raise vertex.ErrorVertex("Faltan parámetros SSM")

    monkeypatch.setattr(vertex, "_leer_config", sin_ssm)
    assert app.handler({"accion": "CLASIFICAR", "descripcion": "lento"})["fuente"] == "reglas"


# ---------- SMS ----------

def test_sms_del_modelo_se_recorta_a_160(http):
    largo = "Identificamos una incidencia en tu zona. " * 10
    http["respuesta"] = respuesta_gemini({"asunto": "Red TeleCorp: aviso", "mensaje": largo})
    r = app.handler({"accion": "REDACTAR_AVISO", "estado": "ATENDIDA", "canal": "SMS"})
    assert r["fuente"] == "gemini"
    assert 0 < len(r["mensaje"]) <= 160


@pytest.mark.parametrize("estado", ["EN_REVISION", "ATENDIDA", "SOLUCIONADA"])
def test_sms_de_plantilla_cabe_en_160_incluso_con_zona_larga(estado):
    r = reglas.redactar_aviso({"estado": estado, "canal": "SMS", "zonaNombre": "Urbanización " + "Muy Larga " * 30})
    assert 0 < len(r["mensaje"]) <= 160


def test_estados_sin_aviso_no_llaman_al_modelo(http):
    for estado in ("REGISTRADA", "CERRADA"):
        r = app.handler({"accion": "REDACTAR_AVISO", "estado": estado, "canal": "SMS"})
        assert r["mensaje"] == "" and r["fuente"] == "reglas"
    assert http["llamadas"] == []


def test_plantillas_respetan_el_sentido_de_eventos_md():
    en_revision = reglas.redactar_aviso({"estado": "EN_REVISION", "canal": "EMAIL"})["mensaje"]
    atendida = reglas.redactar_aviso({"estado": "ATENDIDA", "canal": "EMAIL"})["mensaje"]
    solucionada = reglas.redactar_aviso({"estado": "SOLUCIONADA", "canal": "EMAIL"})["mensaje"]
    assert "evaluando" in en_revision
    assert "atendiendo" in atendida
    assert "restablecido" in solucionada


# ---------- Normalización ----------

def test_normaliza_enums_longitudes_y_confianza(http):
    http["respuesta"] = respuesta_gemini(
        {"tipo": " sin-internet ", "severidad": "crítica", "resumen": "r " * 300, "confianza": 7}
    )
    r = app.handler({"accion": "CLASIFICAR", "descripcion": "x"})
    assert r["fuente"] == "gemini"
    assert r["tipo"] == "SIN_INTERNET"
    assert r["severidad"] == "CRITICA"
    assert len(r["resumen"]) <= 200
    assert r["confianza"] == 1.0


@pytest.mark.parametrize("bruto,esperado", [(-3, 0.0), (0.456, 0.46), ("0.8", 0.8), ("abc", 0.5), (None, 0.5), (True, 0.5)])
def test_confianza_entre_0_y_1(bruto, esperado):
    assert app._confianza(bruto) == esperado


def test_resumir_con_severidad_invalida_usa_reglas(http):
    http["respuesta"] = respuesta_gemini({"resumen": "x", "severidad": "ENORME", "causaProbable": "y"})
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


# ---------- Cliente Vertex: credenciales cacheadas ----------

def test_config_ssm_se_cachea(monkeypatch):
    llamadas = []

    class SsmFalso:
        def get_parameters(self, Names, WithDecryption):
            llamadas.append(Names)
            return {"Parameters": [
                {"Name": "/telecorp/dev/gcp/wif-config", "Value": '{"type": "external_account"}'},
                {"Name": "/telecorp/dev/gcp/project-id", "Value": "p"},
                {"Name": "/telecorp/dev/gcp/region", "Value": "us-east1"},
            ]}

    monkeypatch.setitem(vertex._cache, "config", None)
    monkeypatch.setattr(vertex, "_ssm", lambda: SsmFalso())
    a = vertex._leer_config()
    b = vertex._leer_config()
    assert a is b and len(llamadas) == 1
    assert a["wif"]["type"] == "external_account"
    monkeypatch.setitem(vertex._cache, "config", None)
