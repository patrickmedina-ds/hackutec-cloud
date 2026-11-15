import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("STAGE", "test")

from telecorp_comun import antenas, prioridad  # noqa: E402
from telecorp_comun.estados import (  # noqa: E402
    Estado,
    TransicionInvalida,
    obtener,
    registrar_inicial,
    transicionar,
)


@pytest.mark.parametrize(
    "gb,esperado",
    [(0, "INHABILITADA"), (0.5, "CAIDA"), (59.9, "CAIDA"), (60, "NORMAL"), (129, "NORMAL"), (129.1, "SATURADA")],
)
def test_clasificar_trafico(gb, esperado):
    assert antenas.clasificar_trafico(gb) == esperado


def test_prioridad_crece_con_afectados_y_zona():
    residencial = prioridad.calcular(10, "RESIDENCIAL")
    medica = prioridad.calcular(10, "MEDICA")
    masiva = prioridad.calcular(1000, "RESIDENCIAL", "CRITICA")
    assert residencial < medica
    assert prioridad.nivel(masiva) == "P1"
    assert prioridad.calcular(10**9, "CRITICA", "CRITICA", True) == 100


@pytest.fixture
def ddb():
    with mock_aws():
        cliente = boto3.client("dynamodb")
        cliente.create_table(
            TableName="telecorp-test-incidencias",
            KeySchema=[{"AttributeName": "incidenciaId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "incidenciaId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        cliente.create_table(
            TableName="telecorp-test-historial-estados",
            KeySchema=[
                {"AttributeName": "incidenciaId", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "incidenciaId", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield cliente


def _historial(cliente, incidencia_id):
    r = cliente.query(
        TableName="telecorp-test-historial-estados",
        KeyConditionExpression="incidenciaId = :i",
        ExpressionAttributeValues={":i": {"S": incidencia_id}},
    )
    return r["Items"]


def test_ciclo_completo_con_historial(ddb):
    base = {"incidenciaId": "i-1", "clase": "INDIVIDUAL", "origen": "USUARIO", "zonaId": "Z1", "tipo": "SIN_INTERNET", "lat": -12.1}
    assert registrar_inicial(base, cliente=ddb) is True
    assert registrar_inicial(base, cliente=ddb) is False  # idempotente

    transicionar("i-1", "EN_REVISION", "STEPFN:ciclo-vida", cambios={"prioridad": 30, "severidadIA": "ALTA"}, cliente=ddb)
    transicionar("i-1", "ATENDIDA", "OPERADOR:abc", cliente=ddb)
    final = transicionar("i-1", "SOLUCIONADA", "OPERADOR:abc", "antena reiniciada", cliente=ddb)

    assert final["estado"] == Estado.SOLUCIONADA
    assert "resueltoEn" in final
    guardada = obtener("i-1", cliente=ddb)
    assert guardada["version"] == 4
    assert guardada["severidadIA"] == "ALTA"
    filas = _historial(ddb, "i-1")
    assert [f["estadoNuevo"]["S"] for f in filas] == ["REGISTRADA", "EN_REVISION", "ATENDIDA", "SOLUCIONADA"]
    assert filas[1]["snapshot"]["M"]["prioridad"]["N"] == "30"
    assert "documento" not in filas[0]["snapshot"]["M"]


def test_transicion_invalida(ddb):
    registrar_inicial({"incidenciaId": "i-2", "clase": "INDIVIDUAL", "origen": "USUARIO", "zonaId": "Z1"}, cliente=ddb)
    with pytest.raises(TransicionInvalida):
        transicionar("i-2", "SOLUCIONADA", "SISTEMA", cliente=ddb)
