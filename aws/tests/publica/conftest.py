"""Fixtures de la API pública (moto): tablas clientes, zonas, incidencias, historial y la cola."""

import importlib.util
import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

RAIZ_AWS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ_AWS / "layers" / "comun" / "python"))

os.environ.update(
    {"AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "STAGE": "test"}
)
for _v in ("TABLA_INCIDENCIAS", "TABLA_HISTORIAL", "TABLA_ZONAS", "TABLA_CLIENTES"):
    os.environ.pop(_v, None)

S = {"AttributeType": "S"}


def _gsi(nombre, pk, sk):
    return {
        "IndexName": nombre,
        "KeySchema": [{"AttributeName": pk, "KeyType": "HASH"}, {"AttributeName": sk, "KeyType": "RANGE"}],
        "Projection": {"ProjectionType": "ALL"},
    }


def _crear(cliente, nombre, pk, sk=None, gsis=()):
    attrs = {pk} | ({sk} if sk else set())
    for g in gsis:
        attrs |= {k["AttributeName"] for k in g["KeySchema"]}
    args = {
        "TableName": f"telecorp-test-{nombre}",
        "KeySchema": [{"AttributeName": pk, "KeyType": "HASH"}]
        + ([{"AttributeName": sk, "KeyType": "RANGE"}] if sk else []),
        "AttributeDefinitions": [{"AttributeName": a, **S} for a in sorted(attrs)],
        "BillingMode": "PAY_PER_REQUEST",
    }
    if gsis:
        args["GlobalSecondaryIndexes"] = list(gsis)
    cliente.create_table(**args)


CLIENTES = [
    # numeroServicio, documento, zona, estado
    ("SRV-00000001", "70000001", "LIM-JMA-01", "ACTIVO"),
    ("SRV-00000002", "70000002", "LIM-JMA-01", "ACTIVO"),
    ("SRV-00000013", "70000002", "LIM-MIR-01", "ACTIVO"),
    ("SRV-00000004", "70000004", "LIM-JMA-01", "SUSPENDIDO"),
]


def cargar_modulo():
    ruta = RAIZ_AWS / "src" / "api_publica" / "app.py"
    spec = importlib.util.spec_from_file_location("publica_app", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def entorno(monkeypatch):
    with mock_aws():
        ddb = boto3.client("dynamodb")
        _crear(ddb, "clientes", "numeroServicio", gsis=[_gsi("por-documento", "documento", "numeroServicio")])
        _crear(ddb, "zonas", "zonaId")
        _crear(ddb, "incidencias", "incidenciaId", gsis=[_gsi("por-usuario", "usuarioId", "creadoEn")])
        _crear(ddb, "historial-estados", "incidenciaId", "sk")
        for zona, nombre in (("LIM-JMA-01", "Jesús María"), ("LIM-MIR-01", "Miraflores")):
            ddb.put_item(TableName="telecorp-test-zonas", Item={"zonaId": {"S": zona}, "nombre": {"S": nombre}})
        for num, doc, zona, estado in CLIENTES:
            ddb.put_item(
                TableName="telecorp-test-clientes",
                Item={
                    "numeroServicio": {"S": num}, "documento": {"S": doc}, "zonaId": {"S": zona},
                    "estadoContrato": {"S": estado}, "nombre": {"S": "Cliente " + num},
                    "telefono": {"S": "+51900000" + num[-3:]}, "email": {"S": f"c{num[-4:]}@example.com"},
                    "plan": {"S": "Hogar"}, "creadoEn": {"S": "2026-01-01T00:00:00Z"},
                },
            )
        sqs = boto3.client("sqs")
        url = sqs.create_queue(QueueName="telecorp-test-reportes")["QueueUrl"]
        monkeypatch.setenv("COLA_REPORTES_URL", url)
        app = cargar_modulo()
        yield app, ddb, sqs, url
