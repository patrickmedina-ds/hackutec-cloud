"""Fixtures comunes de las pruebas del núcleo AWS (moto, sin nubes reales)."""

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
    {
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "STAGE": "test",
    }
)
for _v in ("TABLA_INCIDENCIAS", "TABLA_HISTORIAL", "TABLA_ZONA_VENTANA", "TABLA_ZONAS", "TABLA_ANTENAS", "TABLA_METRICAS"):
    os.environ.pop(_v, None)

S = {"AttributeType": "S"}


def _gsi(nombre, pk, sk):
    return {
        "IndexName": nombre,
        "KeySchema": [{"AttributeName": pk, "KeyType": "HASH"}, {"AttributeName": sk, "KeyType": "RANGE"}],
        "Projection": {"ProjectionType": "ALL"},
    }


def _tabla(nombre, pk, sk=None, gsis=(), extra_attrs=()):
    attrs = {pk, *(extra_attrs)} | ({sk} if sk else set())
    for g in gsis:
        attrs |= {k["AttributeName"] for k in g["KeySchema"]}
    llaves = [{"AttributeName": pk, "KeyType": "HASH"}] + ([{"AttributeName": sk, "KeyType": "RANGE"}] if sk else [])
    args = {
        "TableName": f"telecorp-test-{nombre}",
        "KeySchema": llaves,
        "AttributeDefinitions": [{"AttributeName": a, **S} for a in sorted(attrs)],
        "BillingMode": "PAY_PER_REQUEST",
    }
    if gsis:
        args["GlobalSecondaryIndexes"] = list(gsis)
    return args


def crear_tablas(cliente):
    """Las 6 tablas con las mismas claves e índices que aws/template.yaml (contratos/dynamodb.md)."""
    for args in (
        _tabla(
            "incidencias",
            "incidenciaId",
            gsis=[
                _gsi("por-zona", "zonaId", "creadoEn"),
                _gsi("por-estado", "estado", "creadoEn"),
                _gsi("por-colectivo", "incidenteColectivoId", "creadoEn"),
                _gsi("por-usuario", "usuarioId", "creadoEn"),
            ],
        ),
        _tabla("historial-estados", "incidenciaId", "sk"),
        _tabla("zona-ventana", "zonaId", "sk"),
        _tabla("zonas", "zonaId"),
        _tabla("antenas", "antenaId", gsis=[_gsi("por-zona", "zonaId", "antenaId")]),
        _tabla("metricas-antena", "antenaId", "ts"),
    ):
        cliente.create_table(**args)


def cargar_modulo(nombre: str):
    """Importa aws/src/<nombre>/app.py con un nombre único (todos se llaman app.py)."""
    ruta = RAIZ_AWS / "src" / nombre / "app.py"
    spec = importlib.util.spec_from_file_location(f"nucleo_{nombre}", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def ddb():
    with mock_aws():
        cliente = boto3.client("dynamodb")
        crear_tablas(cliente)
        yield cliente
