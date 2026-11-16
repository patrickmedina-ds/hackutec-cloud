"""La plantilla expone /reportes sin autorizador y declara la tabla de clientes."""

from pathlib import Path

import pytest

cfn_yaml = pytest.importorskip("cfnlint.decode.cfn_yaml")
TEMPLATE = cfn_yaml.load(str(Path(__file__).resolve().parents[2] / "template.yaml"))


def test_rutas_publicas_sin_autorizador():
    paths = TEMPLATE["Resources"]["Api"]["Properties"]["DefinitionBody"]["paths"]
    for ruta in ("/reportes", "/reportes/consulta"):
        post = paths[ruta]["post"]
        assert post["security"] == []
        assert post["x-amazon-apigateway-request-validator"] == "cuerpo"
        assert "FuncionApiPublica" in str(post["x-amazon-apigateway-integration"]["uri"])
    assert paths["/incidencias"]["post"]["security"] == [{"CognitoAuth": []}]


def test_tabla_clientes():
    props = TEMPLATE["Resources"]["TablaClientes"]["Properties"]
    assert props["BillingMode"] == "PAY_PER_REQUEST"
    assert props["KeySchema"] == [{"AttributeName": "numeroServicio", "KeyType": "HASH"}]
    assert props["GlobalSecondaryIndexes"][0]["IndexName"] == "por-documento"
    assert props["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"] is True
    assert props["SSESpecification"]["SSEEnabled"] is True
    fn = TEMPLATE["Resources"]["FuncionApiPublica"]["Properties"]
    assert "Role" in fn and "COLA_REPORTES_URL" in fn["Environment"]["Variables"]
