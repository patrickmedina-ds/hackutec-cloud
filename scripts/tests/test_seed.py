"""Pruebas del seed de DynamoDB sobre moto (sin llamar a AWS real)."""

from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

import seed_dynamodb
from conftest import RAIZ

REGION = "us-east-1"
STAGE = "pruebas"
DATOS = RAIZ / "datos"


@pytest.fixture(autouse=True)
def credenciales_falsas(monkeypatch):
    # moto no necesita credenciales reales, pero boto3 exige que existan.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


@pytest.fixture
def dynamodb():
    with mock_aws():
        recurso = boto3.resource("dynamodb", region_name=REGION)
        recurso.create_table(
            TableName=f"telecorp-{STAGE}-zonas",
            KeySchema=[{"AttributeName": "zonaId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "zonaId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        recurso.create_table(
            TableName=f"telecorp-{STAGE}-antenas",
            KeySchema=[{"AttributeName": "antenaId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "antenaId", "AttributeType": "S"},
                {"AttributeName": "zonaId", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "por-zona",
                    "KeySchema": [
                        {"AttributeName": "zonaId", "KeyType": "HASH"},
                        {"AttributeName": "antenaId", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield recurso


def _contar(recurso, tabla):
    return recurso.Table(f"telecorp-{STAGE}-{tabla}").scan(Select="COUNT")["Count"]


def test_siembra_zonas_y_antenas(dynamodb):
    resumen = seed_dynamodb.sembrar(dynamodb, STAGE, DATOS, ahora="2026-11-15T14:03:00Z")

    zonas = len(seed_dynamodb.cargar_json(DATOS / "zonas.json"))
    antenas = len(seed_dynamodb.cargar_json(DATOS / "antenas.json"))
    assert resumen["zonasEscritas"] == zonas == _contar(dynamodb, "zonas")
    assert resumen["antenasEscritas"] == antenas == _contar(dynamodb, "antenas")
    assert resumen["antenasOmitidas"] == 0


def test_antenas_nuevas_salen_en_estado_normal(dynamodb):
    seed_dynamodb.sembrar(dynamodb, STAGE, DATOS, ahora="2026-11-15T14:03:00Z")

    item = dynamodb.Table(f"telecorp-{STAGE}-antenas").scan()["Items"][0]
    assert item["estado"] == "NORMAL"
    assert item["gbPorMinuto"] == Decimal("90")
    assert item["ultimaLectura"] == "2026-11-15T14:03:00Z"
    assert item["cambioEstadoEn"] == "2026-11-15T14:03:00Z"
    assert isinstance(item["lat"], Decimal)


def test_es_idempotente(dynamodb):
    seed_dynamodb.sembrar(dynamodb, STAGE, DATOS, ahora="2026-11-15T14:03:00Z")
    segunda = seed_dynamodb.sembrar(dynamodb, STAGE, DATOS, ahora="2026-11-15T15:00:00Z")

    assert segunda["antenasEscritas"] == 0
    assert segunda["antenasOmitidas"] == len(seed_dynamodb.cargar_json(DATOS / "antenas.json"))
    assert _contar(dynamodb, "zonas") == len(seed_dynamodb.cargar_json(DATOS / "zonas.json"))
    assert _contar(dynamodb, "antenas") == len(seed_dynamodb.cargar_json(DATOS / "antenas.json"))


def test_no_reinicia_el_estado_en_vivo_de_una_antena(dynamodb):
    seed_dynamodb.sembrar(dynamodb, STAGE, DATOS, ahora="2026-11-15T14:03:00Z")
    tabla = dynamodb.Table(f"telecorp-{STAGE}-antenas")
    tabla.update_item(
        Key={"antenaId": "ANT-LIM-MIR-01-01"},
        UpdateExpression="SET estado = :e",
        ExpressionAttributeValues={":e": "CAIDA"},
    )

    seed_dynamodb.sembrar(dynamodb, STAGE, DATOS, ahora="2026-11-15T16:00:00Z")

    assert tabla.get_item(Key={"antenaId": "ANT-LIM-MIR-01-01"})["Item"]["estado"] == "CAIDA"


def test_cli_devuelve_0_con_stage_y_region(dynamodb, capsys):
    codigo = seed_dynamodb.main(["--stage", STAGE, "--region", REGION, "--datos", str(DATOS)])

    assert codigo == 0
    assert "zonas escritas" in capsys.readouterr().out


def test_cli_devuelve_1_si_la_tabla_no_existe(dynamodb, capsys):
    codigo = seed_dynamodb.main(["--stage", "inexistente", "--region", REGION, "--datos", str(DATOS)])

    assert codigo == 1
    assert "Error al sembrar" in capsys.readouterr().err
