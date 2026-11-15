"""Validación estática de las definiciones ASL y del template de orquestación."""

import json
import re
from pathlib import Path

import pytest
import yaml

RAIZ_AWS = Path(__file__).resolve().parents[2]
TEMPLATE = RAIZ_AWS / "stacks" / "orquestacion.yaml"
SUSTITUCION = re.compile(r"\$\{([A-Za-z0-9_]+)\}")
EXPRESION = re.compile(r"^\{%.*%\}$", re.S)


class _CargadorCfn(yaml.SafeLoader):
    """Acepta las etiquetas intrínsecas de CloudFormation (!Sub, !Ref, ...)."""


def _intrinseca(cargador, sufijo, nodo):
    if isinstance(nodo, yaml.ScalarNode):
        valor = cargador.construct_scalar(nodo)
    elif isinstance(nodo, yaml.SequenceNode):
        valor = cargador.construct_sequence(nodo)
    else:
        valor = cargador.construct_mapping(nodo)
    return {sufijo: valor}


_CargadorCfn.add_multi_constructor("!", _intrinseca)


@pytest.fixture(scope="module")
def template():
    return yaml.load(TEMPLATE.read_text(), Loader=_CargadorCfn)


@pytest.fixture(scope="module")
def maquinas(template):
    return {
        nombre: recurso["Properties"]
        for nombre, recurso in template["Resources"].items()
        if recurso["Type"] == "AWS::Serverless::StateMachine"
    }


def _asl(propiedades):
    return (TEMPLATE.parent / propiedades["DefinitionUri"]).resolve()


def test_parametros_exactos(template):
    assert set(template["Parameters"]) == {"Stage", "ComunLayerArn", "RolLabArn"}


def test_tres_maquinas_con_nombres_del_contrato(maquinas):
    nombres = {p["Name"]["Sub"]: p["Type"] for p in maquinas.values()}
    assert nombres == {
        "telecorp-${Stage}-ciclo-vida": "EXPRESS",
        "telecorp-${Stage}-colectivo": "STANDARD",
        "telecorp-${Stage}-recurrencia": "STANDARD",
    }


def test_logging_y_xray(maquinas):
    for p in maquinas.values():
        assert p["Tracing"]["Enabled"] is True
        assert p["Logging"]["Level"] == "ALL"


def _estados(definicion):
    """Todos los estados, incluidos los de Parallel y Map, con su contenedor."""
    for nombre, estado in definicion["States"].items():
        yield definicion, nombre, estado
        for rama in estado.get("Branches", []):
            yield from _estados(rama)
        if "ItemProcessor" in estado:
            yield from _estados(estado["ItemProcessor"])


def _cadenas(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _cadenas(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _cadenas(v)


@pytest.mark.parametrize("archivo", ["ciclo_vida", "colectivo", "recurrencia"])
def test_asl_valido_y_sustituciones_definidas(archivo, maquinas):
    propiedades = next(p for p in maquinas.values() if _asl(p).name == f"{archivo}.asl.json")
    texto = _asl(propiedades).read_text()
    definicion = json.loads(texto)  # JSON válido

    usadas = set(SUSTITUCION.findall(texto))
    definidas = set(propiedades["DefinitionSubstitutions"])
    assert usadas <= definidas, f"Sustituciones sin definir: {usadas - definidas}"
    assert definidas <= usadas, f"Sustituciones que sobran: {definidas - usadas}"

    assert definicion["QueryLanguage"] == "JSONata"
    assert definicion["StartAt"] in definicion["States"]
    for contenedor, nombre, estado in _estados(definicion):
        for destino in [estado.get("Next"), estado.get("Default")] + [
            c["Next"] for c in estado.get("Choices", []) + estado.get("Catch", [])
        ]:
            assert destino is None or destino in contenedor["States"], f"{nombre} → {destino} no existe"
        terminal = estado.get("End") or estado["Type"] in ("Succeed", "Fail", "Choice")
        assert terminal or "Next" in estado, f"{nombre} no termina ni continúa"
        # JSONPath no se mezcla con JSONata.
        assert not {"Parameters", "ResultPath", "ResultSelector", "InputPath", "OutputPath"} & set(estado)

    for cadena in _cadenas(definicion):
        if "{%" in cadena:
            assert EXPRESION.match(cadena), f"Expresión JSONata mal delimitada: {cadena}"


def _reglas(template):
    return {r["Properties"]["Name"]["Sub"]: r["Properties"] for r in template["Resources"].values()
            if r["Type"] == "AWS::Events::Rule"}


def test_reglas_de_eventbridge(template, maquinas):
    reglas = _reglas(template)
    patron = reglas["telecorp-${Stage}-ciclo-vida"]["EventPattern"]
    assert patron["detail-type"] == ["Incidencia.CambioEstado"]
    assert patron["detail"]["snapshot"]["clase"] == ["INDIVIDUAL"]
    assert "CERRADA" not in patron["detail"]["estadoNuevo"]

    tipos = {reglas[n]["EventPattern"]["detail-type"][0]
             for n in ("telecorp-${Stage}-colectivo-umbral", "telecorp-${Stage}-colectivo-antena")}
    assert tipos == {"Zona.UmbralSuperado", "Antena.CambioEstado"}
    for regla in reglas.values():
        (destino,) = regla["Targets"]
        assert destino["RoleArn"] == {"Ref": "RolLabArn"}
        assert "DeadLetterConfig" in destino

    recurrencia = next(p for p in maquinas.values() if p["Name"]["Sub"].endswith("recurrencia"))
    horario = recurrencia["Events"]["Diario"]["Properties"]
    assert horario["ScheduleExpression"] == "cron(0 2 * * ? *)"
    assert horario["ScheduleExpressionTimezone"] == "America/Lima"
    assert horario["RoleArn"] == {"Ref": "RolLabArn"}


def test_todo_usa_labrole(template):
    for nombre, recurso in template["Resources"].items():
        assert not recurso["Type"].startswith("AWS::IAM::"), nombre
        if recurso["Type"] in ("AWS::Serverless::Function", "AWS::Serverless::StateMachine"):
            assert recurso["Properties"]["Role"] == {"Ref": "RolLabArn"}, nombre
            assert "Policies" not in recurso["Properties"], nombre


def _ciclo_vida():
    return json.loads((RAIZ_AWS / "statemachines" / "ciclo_vida.asl.json").read_text())


def test_ciclo_vida_publica_notificacion_solicitada():
    texto = (RAIZ_AWS / "statemachines" / "ciclo_vida.asl.json").read_text()
    assert "sns:publish" not in texto and "sesv2" not in texto and "ssm:" not in texto
    assert "Notificacion.Fallida" not in texto
    notificar = _ciclo_vida()["States"]["Notificar"]
    assert notificar["Resource"] == "arn:aws:states:::events:putEvents"
    entradas = notificar["Arguments"]["Entries"]
    assert entradas.count("'DetailType': 'Notificacion.Solicitada'") == 2
    assert entradas.count("'Source': 'telecorp.notificaciones'") == 2
    assert "'canal': 'SMS', 'destino': $telefono" in entradas
    assert "'canal': 'EMAIL', 'destino': $email" in entradas
    for campo in ("incidenciaId", "estado", "canal", "destino", "asunto", "mensaje"):
        assert entradas.count(f"'{campo}':") == 2


def test_ciclo_vida_redacta_con_ia_y_respaldo():
    redactar = _ciclo_vida()["States"]["RedactarAviso"]
    assert redactar["Arguments"]["Payload"]["accion"] == "REDACTAR_AVISO"
    assert redactar["Next"] == "Notificar"
    assert redactar["Catch"][0]["Next"] == "Notificar"
    assert "El servicio fue restablecido." in redactar["Catch"][0]["Assign"]["mensaje"]


def test_lambdas_usan_la_capa_comun(template):
    assert template["Globals"]["Function"]["Layers"] == [{"Ref": "ComunLayerArn"}]
    handlers = {r["Properties"]["Handler"] for r in template["Resources"].values()
                if r["Type"] == "AWS::Serverless::Function"}
    for handler in handlers:
        modulo = handler.split(".")[0]
        assert (RAIZ_AWS / "src" / "sfn_tareas" / f"{modulo}.py").exists()
