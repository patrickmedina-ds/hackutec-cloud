"""Fixtures del bloque A: tablas DynamoDB de contratos/dynamodb.md en moto."""

import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

RAIZ_AWS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ_AWS / "layers" / "comun" / "python"))
sys.path.insert(0, str(RAIZ_AWS / "src" / "sfn_tareas"))

os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "test"
os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
os.environ["STAGE"] = "test"
for variable in (
    "TABLA_INCIDENCIAS",
    "TABLA_HISTORIAL",
    "TABLA_ZONA_VENTANA",
    "TABLA_ZONAS",
    "TABLA_ANTENAS",
    "TABLA_METRICAS",
    "BUS_EVENTOS",
):
    os.environ.pop(variable, None)


def _gsi(nombre, pk, sk):
    return {
        "IndexName": nombre,
        "KeySchema": [{"AttributeName": pk, "KeyType": "HASH"}, {"AttributeName": sk, "KeyType": "RANGE"}],
        "Projection": {"ProjectionType": "ALL"},
    }


def _crear_tablas(ddb):
    s = lambda n: {"AttributeName": n, "AttributeType": "S"}  # noqa: E731
    ddb.create_table(
        TableName="telecorp-test-incidencias",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "incidenciaId", "KeyType": "HASH"}],
        AttributeDefinitions=[
            s("incidenciaId"), s("zonaId"), s("creadoEn"), s("estado"), s("incidenteColectivoId"), s("usuarioId")
        ],
        GlobalSecondaryIndexes=[
            _gsi("por-zona", "zonaId", "creadoEn"),
            _gsi("por-estado", "estado", "creadoEn"),
            _gsi("por-colectivo", "incidenteColectivoId", "creadoEn"),
            _gsi("por-usuario", "usuarioId", "creadoEn"),
        ],
    )
    for nombre, pk, sk in (
        ("historial-estados", "incidenciaId", "sk"),
        ("zona-ventana", "zonaId", "sk"),
        ("metricas-antena", "antenaId", "ts"),
    ):
        ddb.create_table(
            TableName=f"telecorp-test-{nombre}",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": pk, "KeyType": "HASH"}, {"AttributeName": sk, "KeyType": "RANGE"}],
            AttributeDefinitions=[s(pk), s(sk)],
        )
    ddb.create_table(
        TableName="telecorp-test-zonas",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "zonaId", "KeyType": "HASH"}],
        AttributeDefinitions=[s("zonaId")],
    )
    ddb.create_table(
        TableName="telecorp-test-antenas",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "antenaId", "KeyType": "HASH"}],
        AttributeDefinitions=[s("antenaId"), s("zonaId")],
        GlobalSecondaryIndexes=[_gsi("por-zona", "zonaId", "antenaId")],
    )


@pytest.fixture
def ddb():
    with mock_aws():
        cliente = boto3.client("dynamodb", region_name="us-east-1")
        _crear_tablas(cliente)
        cliente.put_item(
            TableName="telecorp-test-zonas",
            Item={
                "zonaId": {"S": "LIM-MIR-01"},
                "nombre": {"S": "Miraflores 01"},
                "tipoZona": {"S": "MEDICA"},
                "usuariosEstimados": {"N": "500"},
            },
        )
        yield cliente


@pytest.fixture
def crear_reporte(ddb):
    """Registra un reporte INDIVIDUAL en REGISTRADA con la capa común."""
    from telecorp_comun.estados import registrar_inicial

    def _crear(incidencia_id, **extra):
        registrar_inicial(
            {
                "incidenciaId": incidencia_id,
                "clase": "INDIVIDUAL",
                "origen": "USUARIO",
                "zonaId": "LIM-MIR-01",
                "tipo": "SIN_INTERNET",
                "descripcion": "No tengo internet",
                "telefono": "+51987654321",
                "usuarioId": f"u-{incidencia_id}",
                **extra,
            },
            cliente=ddb,
        )
        return incidencia_id

    return _crear


def historial(ddb, incidencia_id):
    r = ddb.query(
        TableName="telecorp-test-historial-estados",
        KeyConditionExpression="incidenciaId = :i",
        ExpressionAttributeValues={":i": {"S": incidencia_id}},
    )
    return [i["estadoNuevo"]["S"] for i in sorted(r["Items"], key=lambda i: i["sk"]["S"])]


@pytest.fixture
def leer_historial(ddb):
    """Estados registrados en historial-estados, en orden."""
    return lambda incidencia_id: historial(ddb, incidencia_id)
