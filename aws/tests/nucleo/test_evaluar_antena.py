"""evaluar_antena: métricas siempre, antenas solo cuando cambia el estado."""

import base64
import json
from datetime import datetime

import pytest

from conftest import cargar_modulo

ANTENA = "ANT-LIM-MIR-01-03"
ZONA = "LIM-MIR-01"


@pytest.fixture
def app(ddb):
    modulo = cargar_modulo("evaluar_antena")
    modulo._cliente = None
    return modulo


def registro(n: int, gb, ts: str, antena: str = ANTENA) -> dict:
    carga = {"antenaId": antena, "zonaId": ZONA, "ts": ts, "gbPorMinuto": gb}
    return {
        "kinesis": {
            "sequenceNumber": f"seq{n}",
            "data": base64.b64encode(json.dumps(carga).encode()).decode(),
        }
    }


def sembrar(ddb, estado="NORMAL"):
    ddb.put_item(
        TableName="telecorp-test-antenas",
        Item={
            "antenaId": {"S": ANTENA},
            "zonaId": {"S": ZONA},
            "estado": {"S": estado},
            "gbPorMinuto": {"N": "100"},
            "ultimaLectura": {"S": "2026-11-15T14:00:00Z"},
            "cambioEstadoEn": {"S": "2026-11-15T14:00:00Z"},
        },
    )


def antena(ddb, antena_id=ANTENA):
    return ddb.get_item(TableName="telecorp-test-antenas", Key={"antenaId": {"S": antena_id}})["Item"]


def metricas(ddb):
    return ddb.query(
        TableName="telecorp-test-metricas-antena",
        KeyConditionExpression="antenaId = :a",
        ExpressionAttributeValues={":a": {"S": ANTENA}},
    )["Items"]


def test_estado_normal_no_escribe_en_antenas_pero_si_metricas(app, ddb):
    sembrar(ddb)
    r = app.lambda_handler({"Records": [registro(1, 100, "2026-11-15T14:01:00Z")]}, None)
    assert r == {"batchItemFailures": []}
    assert antena(ddb)["cambioEstadoEn"]["S"] == "2026-11-15T14:00:00Z"  # intacta
    assert antena(ddb)["gbPorMinuto"]["N"] == "100"
    m = metricas(ddb)
    assert len(m) == 1 and m[0]["estado"]["S"] == "NORMAL"
    esperado = int(datetime.fromisoformat("2026-11-15T14:01:00+00:00").timestamp()) + 30 * 86400
    assert int(m[0]["ttl"]["N"]) == esperado


def test_escribe_solo_cuando_cambia(app, ddb):
    sembrar(ddb)
    app.lambda_handler({"Records": [registro(1, 30, "2026-11-15T14:01:00Z")]}, None)
    a = antena(ddb)
    assert a["estado"]["S"] == "CAIDA" and a["cambioEstadoEn"]["S"] == "2026-11-15T14:01:00Z"
    assert a["gbPorMinuto"]["N"] == "30"

    # Otra lectura en el mismo estado (en otra invocación): no toca `antenas`.
    app.lambda_handler({"Records": [registro(2, 20, "2026-11-15T14:02:00Z")]}, None)
    a = antena(ddb)
    assert a["cambioEstadoEn"]["S"] == "2026-11-15T14:01:00Z"
    assert a["gbPorMinuto"]["N"] == "30"
    assert len(metricas(ddb)) == 2  # las métricas sí se guardan siempre

    # Cambio real: saturada.
    app.lambda_handler({"Records": [registro(3, 150.5, "2026-11-15T14:03:00Z")]}, None)
    a = antena(ddb)
    assert a["estado"]["S"] == "SATURADA" and a["cambioEstadoEn"]["S"] == "2026-11-15T14:03:00Z"


def test_cuenta_actualizaciones_reales(app, ddb, monkeypatch):
    """Dentro de un lote con varias lecturas del mismo estado, solo hay una UpdateItem."""
    sembrar(ddb)
    llamadas = []
    original = app.actualizar_estado
    monkeypatch.setattr(app, "actualizar_estado", lambda l: llamadas.append(l["estado"]) or original(l))
    lote = [registro(i, 30 - i, f"2026-11-15T14:0{i}:00Z") for i in range(1, 5)]
    lote.append(registro(5, 0, "2026-11-15T14:06:00Z"))  # INHABILITADA
    lote.append(registro(6, 100, "2026-11-15T14:07:00Z"))  # vuelve a NORMAL
    app.lambda_handler({"Records": lote}, None)
    assert llamadas == ["CAIDA", "INHABILITADA", "NORMAL"]
    assert antena(ddb)["estado"]["S"] == "NORMAL"


def test_antena_desconocida_se_siembra_normal_y_genera_modify(app, ddb):
    app.lambda_handler({"Records": [registro(1, 10, "2026-11-15T14:01:00Z")]}, None)
    a = antena(ddb)
    assert a["estado"]["S"] == "CAIDA" and a["zonaId"]["S"] == ZONA


def test_lectura_invalida_se_descarta(app, ddb):
    sembrar(ddb)
    mala = {"kinesis": {"sequenceNumber": "s0", "data": base64.b64encode(b"{no es json").decode()}}
    sin_gb = registro(2, "x", "2026-11-15T14:01:00Z")
    r = app.lambda_handler({"Records": [mala, sin_gb, registro(3, 100, "2026-11-15T14:02:00Z")]}, None)
    assert r == {"batchItemFailures": []}
    assert len(metricas(ddb)) == 1


def test_fallo_de_dynamodb_reporta_registro_fallido(app, ddb, monkeypatch):
    sembrar(ddb)

    def falla(_):
        raise RuntimeError("DynamoDB no disponible")

    monkeypatch.setattr(app, "escribir_metrica", falla)
    r = app.lambda_handler({"Records": [registro(1, 100, "2026-11-15T14:01:00Z"), registro(2, 100, "2026-11-15T14:02:00Z")]}, None)
    assert r == {"batchItemFailures": [{"itemIdentifier": "seq1"}]}
