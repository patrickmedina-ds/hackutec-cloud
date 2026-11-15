"""Learner Lab: no se pueden crear roles IAM.

Traduce cada template con samtranslator (la misma transformación que hace
CloudFormation con AWS::Serverless-2016-10-31) y falla si aparece cualquier
recurso AWS::IAM::*, incluidos los roles implícitos que SAM genera para
`Policies`, `Events`, `AutoPublishAlias`, etc.
"""

import copy
from pathlib import Path

import pytest

cfn_yaml = pytest.importorskip("cfnlint.decode.cfn_yaml")
transform_mod = pytest.importorskip("samtranslator.translator.transform")

RAIZ_AWS = Path(__file__).resolve().parents[2]
TEMPLATES = ["template.yaml", "stacks/orquestacion.yaml", "stacks/analitica.yaml"]
# Los ajusta otro equipo; se comprueban igual porque el despliegue es uno solo.
TEMPLATES_PARALELOS = ["stacks/puente.yaml", "stacks/ia.yaml"]

S3_FALSO = {"Bucket": "bucket-pruebas", "Key": "artefacto.zip"}


class _SinPoliticasAdministradas:
    def load(self):
        return {}


def _artefactos_remotos(template):
    """Sustituye rutas locales (que resuelve `sam build`) por ubicaciones S3 ficticias."""
    t = copy.deepcopy(template)
    for recurso in t.get("Resources", {}).values():
        props = recurso.get("Properties") or {}
        tipo = recurso.get("Type")
        if tipo == "AWS::Serverless::Function" and isinstance(props.get("CodeUri", ""), str):
            props["CodeUri"] = dict(S3_FALSO)
        elif tipo == "AWS::Serverless::LayerVersion":
            props["ContentUri"] = dict(S3_FALSO)
        elif tipo == "AWS::Serverless::StateMachine" and "DefinitionUri" in props:
            props["DefinitionUri"] = dict(S3_FALSO)
        elif tipo == "AWS::Serverless::Application":
            props["Location"] = "https://s3.amazonaws.com/bucket-pruebas/stack.yaml"
        elif tipo == "AWS::Serverless::Api" and isinstance(props.get("DefinitionUri"), str):
            props["DefinitionUri"] = dict(S3_FALSO)
    globales = t.get("Globals", {}).get("Function", {})
    if isinstance(globales.get("CodeUri"), str):
        globales["CodeUri"] = dict(S3_FALSO)
    return t


def _traducir(ruta):
    template = cfn_yaml.load(str(RAIZ_AWS / ruta))
    return transform_mod.transform(
        _artefactos_remotos(dict(template)),
        {"AWS::Region": "us-east-1", "AWS::AccountId": "123456789012", "AWS::Partition": "aws"},
        _SinPoliticasAdministradas(),
    )


def _recursos_iam(traducido):
    return sorted(
        f"{nombre} ({r['Type']})"
        for nombre, r in traducido["Resources"].items()
        if r["Type"].startswith("AWS::IAM::")
    )


@pytest.mark.parametrize("ruta", TEMPLATES)
def test_template_sin_recursos_iam(ruta):
    traducido = _traducir(ruta)
    assert _recursos_iam(traducido) == []
    for nombre, r in traducido["Resources"].items():
        if r["Type"] in ("AWS::Lambda::Function", "AWS::StepFunctions::StateMachine"):
            assert "Role" in r["Properties"] or "RoleArn" in r["Properties"], nombre


@pytest.mark.parametrize("ruta", TEMPLATES_PARALELOS)
def test_stacks_paralelos_sin_recursos_iam(ruta):
    assert _recursos_iam(_traducir(ruta)) == []


def test_raiz_pasa_rol_lab_a_los_stacks_anidados():
    template = cfn_yaml.load(str(RAIZ_AWS / "template.yaml"))
    anidados = {n: r for n, r in template["Resources"].items() if r["Type"] == "AWS::Serverless::Application"}
    assert len(anidados) == 4
    for nombre, recurso in anidados.items():
        assert set(recurso["Properties"]["Parameters"]) == {"Stage", "ComunLayerArn", "RolLabArn"}, nombre
    assert template["Parameters"]["RolLabNombre"]["Default"] == "LabRole"
