"""Pruebas de clasificar-ia: cliente Bedrock simulado con botocore Stubber (sin red ni nubes)."""

import sys
import time
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ReadTimeoutError
from botocore.stub import Stubber

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "clasificar_ia"))

import app  # noqa: E402
import bedrock  # noqa: E402
import reglas  # noqa: E402

MODELO = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def respuesta_tool(entrada, nombre=bedrock.NOMBRE_HERRAMIENTA):
    contenido = [{"toolUse": {"toolUseId": "t1", "name": nombre, "input": entrada}}]
    return {
        "output": {"message": {"role": "assistant", "content": contenido}},
        "stopReason": "tool_use",
        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        "metrics": {"latencyMs": 1},
    }


def respuesta_texto(texto):
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": texto}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        "metrics": {"latencyMs": 1},
    }


@pytest.fixture
def ia(monkeypatch):
    """Cliente bedrock-runtime con Stubber; captura los parámetros de cada llamada."""
    monkeypatch.delenv("MODELO_IA", raising=False)
    cliente = boto3.client("bedrock-runtime", region_name="us-east-1",
                           aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(cliente)
    monkeypatch.setitem(bedrock._cache, "cliente", cliente)
    llamadas = []
    original = cliente.converse

    def espia(**kwargs):
        llamadas.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(cliente, "converse", espia)
    stub.activate()
    stub.llamadas = llamadas
    yield stub
    stub.deactivate()


# ---------- Vía feliz (Bedrock simulado) ----------

def test_clasificar_feliz(ia):
    ia.add_response("converse", respuesta_tool(
        {"tipo": "SIN_INTERNET", "severidad": "ALTA", "resumen": "Sin internet en clínica", "confianza": 0.86}))
    r = app.handler({"accion": "CLASIFICAR", "incidenciaId": "i1", "tipo": "LENTITUD",
                     "descripcion": "no hay internet en la clínica", "zonaId": "LIM-MIR-01", "tipoZona": "MEDICA"})
    assert r == {"tipo": "SIN_INTERNET", "severidad": "ALTA", "resumen": "Sin internet en clínica",
                 "confianza": 0.86, "fuente": "bedrock"}
    llamada = ia.llamadas[0]
    assert llamada["modelId"] == MODELO
    assert llamada["inferenceConfig"]["temperature"] <= 0.3
    tc = llamada["toolConfig"]
    nombre = tc["tools"][0]["toolSpec"]["name"]
    assert tc["toolChoice"] == {"tool": {"name": nombre}}
    esquema = tc["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert set(esquema["properties"]) == {"tipo", "severidad", "resumen", "confianza"}
    assert esquema["properties"]["tipo"]["enum"] == list(reglas.TIPOS)
    assert esquema["properties"]["severidad"]["enum"] == list(reglas.SEVERIDADES)
    assert "no hay internet en la clínica" in llamada["messages"][0]["content"][0]["text"]


def test_resumir_colectivo_feliz(ia):
    ia.add_response("converse", respuesta_tool(
        {"resumen": "Caída de antena en Miraflores", "severidad": "CRITICA", "causaProbable": "Antena ANT-1 caída"}))
    r = app.handler({"accion": "RESUMIR_COLECTIVO", "incidenteColectivoId": "COL-1", "zonaId": "LIM-MIR-01",
                     "tipoZona": "MEDICA", "descripciones": ["sin señal", "no navega"],
                     "antenas": [{"antenaId": "ANT-1", "estado": "CAIDA"}]})
    assert r == {"resumen": "Caída de antena en Miraflores", "severidad": "CRITICA",
                 "causaProbable": "Antena ANT-1 caída", "fuente": "bedrock"}
    esquema = ia.llamadas[0]["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert set(esquema["properties"]) == {"resumen", "severidad", "causaProbable"}
    assert esquema["properties"]["severidad"]["enum"] == list(reglas.SEVERIDADES)


def test_redactar_aviso_feliz(ia):
    ia.add_response("converse", respuesta_tool(
        {"asunto": "Red TeleCorp: incidencia identificada en tu zona",
         "mensaje": "Identificamos una incidencia en Miraflores y un equipo la está atendiendo."}))
    r = app.handler({"accion": "REDACTAR_AVISO", "estado": "ATENDIDA", "zonaNombre": "Miraflores", "canal": "EMAIL"})
    assert r["fuente"] == "bedrock"
    assert r["asunto"].startswith("Red TeleCorp")
    assert "atendiendo" in r["mensaje"]
    esquema = ia.llamadas[0]["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert set(esquema["properties"]) == {"asunto", "mensaje"}


def test_modelo_configurable_por_variable_de_entorno(ia, monkeypatch):
    monkeypatch.setenv("MODELO_IA", "us.anthropic.otro-modelo-v1:0")
    ia.add_response("converse", respuesta_tool({"asunto": "Red TeleCorp: aviso", "mensaje": "hola"}))
    app.handler({"accion": "REDACTAR_AVISO", "estado": "ATENDIDA", "canal": "EMAIL"})
    assert ia.llamadas[0]["modelId"] == "us.anthropic.otro-modelo-v1:0"


# ---------- Respaldo ----------

FALLAS = [
    ("error", lambda ia: ia.add_client_error("converse", "ThrottlingException", "limite", 429)),
    ("acceso", lambda ia: ia.add_client_error("converse", "AccessDeniedException", "sin acceso", 403)),
    ("timeout", lambda ia: ia.add_client_error("converse", "ModelTimeoutException", "tarda", 408)),
    ("sin_herramienta", lambda ia: ia.add_response("converse", respuesta_texto("no uso la herramienta"))),
    ("herramienta_ajena", lambda ia: ia.add_response("converse", respuesta_tool({"tipo": "OTRO"}, nombre="otra"))),
    ("input_no_objeto", lambda ia: ia.add_response("converse", respuesta_tool("{texto"))),
    ("input_lista", lambda ia: ia.add_response("converse", respuesta_tool([1, 2, 3]))),
    ("enum_invalido", lambda ia: ia.add_response("converse", respuesta_tool(
        {"tipo": "INVENTADO", "severidad": "ALTA", "resumen": "x", "confianza": 1}))),
    ("incompleto", lambda ia: ia.add_response("converse", respuesta_tool({"tipo": "SIN_INTERNET"}))),
]


@pytest.mark.parametrize("preparar", [f[1] for f in FALLAS], ids=[f[0] for f in FALLAS])
def test_clasificar_respaldo_ante_fallas(ia, preparar):
    preparar(ia)
    r = app.handler({"accion": "CLASIFICAR", "tipo": "OTRO", "descripcion": "Sin internet desde ayer", "tipoZona": "RESIDENCIAL"})
    assert r["fuente"] == "reglas"
    assert r["tipo"] == "SIN_INTERNET"
    assert r["severidad"] in reglas.SEVERIDADES
    assert 0 <= r["confianza"] <= 1


def test_respaldo_ante_timeout_de_lectura_de_red(monkeypatch):
    class Cliente:
        def converse(self, **kw):
            raise ReadTimeoutError(endpoint_url="https://bedrock-runtime")

    monkeypatch.setitem(bedrock._cache, "cliente", Cliente())
    r = app.handler({"accion": "CLASIFICAR", "descripcion": "no hay internet"})
    assert r["fuente"] == "reglas" and r["tipo"] == "SIN_INTERNET"


def test_respaldo_en_las_tres_acciones_con_error(ia):
    ia.add_client_error("converse", "InternalServerException", "boom", 500)
    ia.add_client_error("converse", "InternalServerException", "boom", 500)
    c = app.handler({"accion": "RESUMIR_COLECTIVO", "zonaId": "Z", "descripciones": ["a"],
                     "antenas": [{"antenaId": "A1", "estado": "CAIDA"}, {"antenaId": "A2", "estado": "CAIDA"}]})
    assert c["fuente"] == "reglas" and c["severidad"] == "CRITICA" and "A1" in c["causaProbable"]
    a = app.handler({"accion": "REDACTAR_AVISO", "estado": "SOLUCIONADA", "zonaNombre": "Miraflores", "canal": "EMAIL"})
    assert a["fuente"] == "reglas" and "restablecido" in a["mensaje"] and a["asunto"]


def test_respaldo_por_tiempo_excedido(monkeypatch):
    def lenta(*args, **kwargs):
        time.sleep(2)
        return {"tipo": "OTRO", "severidad": "BAJA", "resumen": "x", "confianza": 1}

    monkeypatch.setattr(bedrock, "generar_json", lenta)
    monkeypatch.setattr(app, "LIMITE_IA_S", 0.2)
    inicio = time.monotonic()
    r = app.handler({"accion": "CLASIFICAR", "descripcion": "no hay internet"})
    assert time.monotonic() - inicio < 1.5
    assert r["fuente"] == "reglas" and r["tipo"] == "SIN_INTERNET"


def test_nunca_lanza_excepcion(monkeypatch):
    def explota(*a, **k):
        raise RuntimeError("fallo inesperado")

    monkeypatch.setattr(bedrock, "generar_json", explota)
    assert app.handler({"accion": "CLASIFICAR"})["fuente"] == "reglas"
    assert app.handler(None)["fuente"] == "reglas"
    assert app.handler({"accion": "NO_EXISTE"})["fuente"] == "reglas"
    assert app.handler({"accion": "RESUMIR_COLECTIVO", "descripciones": None, "antenas": "x"})["fuente"] == "reglas"
    monkeypatch.setattr(app, "procesar", explota)
    assert app.handler({"accion": "CLASIFICAR", "descripcion": "hospital"})["severidad"] == "CRITICA"


# ---------- SMS ----------

def test_sms_del_modelo_se_recorta_a_160(ia):
    largo = "Identificamos una incidencia en tu zona. " * 10
    ia.add_response("converse", respuesta_tool({"asunto": "Red TeleCorp: aviso", "mensaje": largo}))
    r = app.handler({"accion": "REDACTAR_AVISO", "estado": "ATENDIDA", "canal": "SMS"})
    assert r["fuente"] == "bedrock"
    assert 0 < len(r["mensaje"]) <= 160


@pytest.mark.parametrize("estado", ["EN_REVISION", "ATENDIDA", "SOLUCIONADA"])
def test_sms_de_plantilla_cabe_en_160_incluso_con_zona_larga(estado):
    r = reglas.redactar_aviso({"estado": estado, "canal": "SMS", "zonaNombre": "Urbanización " + "Muy Larga " * 30})
    assert 0 < len(r["mensaje"]) <= 160


def test_estados_sin_aviso_no_llaman_al_modelo(ia):
    for estado in ("REGISTRADA", "CERRADA"):
        r = app.handler({"accion": "REDACTAR_AVISO", "estado": estado, "canal": "SMS"})
        assert r["mensaje"] == "" and r["fuente"] == "reglas"
    assert ia.llamadas == []


def test_plantillas_respetan_el_sentido_de_eventos_md():
    en_revision = reglas.redactar_aviso({"estado": "EN_REVISION", "canal": "EMAIL"})["mensaje"]
    atendida = reglas.redactar_aviso({"estado": "ATENDIDA", "canal": "EMAIL"})["mensaje"]
    solucionada = reglas.redactar_aviso({"estado": "SOLUCIONADA", "canal": "EMAIL"})["mensaje"]
    assert "evaluando" in en_revision
    assert "atendiendo" in atendida
    assert "restablecido" in solucionada


# ---------- Normalización ----------

def test_normaliza_enums_longitudes_y_confianza(ia):
    ia.add_response("converse", respuesta_tool(
        {"tipo": " sin-internet ", "severidad": "crítica", "resumen": "r " * 300, "confianza": 7}))
    r = app.handler({"accion": "CLASIFICAR", "descripcion": "x"})
    assert r["fuente"] == "bedrock"
    assert r["tipo"] == "SIN_INTERNET"
    assert r["severidad"] == "CRITICA"
    assert len(r["resumen"]) <= 200
    assert r["confianza"] == 1.0


@pytest.mark.parametrize("bruto,esperado", [(-3, 0.0), (0.456, 0.46), ("0.8", 0.8), ("abc", 0.5), (None, 0.5), (True, 0.5)])
def test_confianza_entre_0_y_1(bruto, esperado):
    assert app._confianza(bruto) == esperado


def test_resumir_con_severidad_invalida_usa_reglas(ia):
    ia.add_response("converse", respuesta_tool({"resumen": "x", "severidad": "ENORME", "causaProbable": "y"}))
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
