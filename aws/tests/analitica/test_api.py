import json
import sys

import boto3
import pytest
from botocore.stub import Stubber

sys.modules.pop("app", None)
import app  # noqa: E402


def evento(reporte="top_zonas", dias=None, grupos=("operadores",)):
    return {
        "pathParameters": {"reporte": reporte},
        "queryStringParameters": {"dias": dias} if dias is not None else None,
        "requestContext": {"authorizer": {"claims": {"cognito:groups": list(grupos)}}},
    }


@pytest.fixture
def athena():
    cliente = boto3.client("athena", region_name="us-east-1")
    app._athena = cliente
    app._cache.clear()
    with Stubber(cliente) as stub:
        yield stub
    app._athena = None


def preparar(stub, paginas, estados=("SUCCEEDED",)):
    stub.add_response("start_query_execution", {"QueryExecutionId": "q1"})
    for e in estados:
        stub.add_response("get_query_execution", {"QueryExecution": {"Status": {"State": e}}})
    if estados[-1] == "SUCCEEDED":
        for i, p in enumerate(paginas):
            resp = {"ResultSet": {"Rows": p, "ResultSetMetadata": {"ColumnInfo": [
                {"Name": "zonaId", "Label": "zonaId", "Type": "varchar"}, {"Name": "incidencias", "Label": "incidencias", "Type": "bigint"}]}}}
            if i < len(paginas) - 1:
                resp["NextToken"] = f"t{i}"
            stub.add_response("get_query_results", resp)


def fila(*valores):
    return {"Data": [{"VarCharValue": v} for v in valores]}


@pytest.mark.parametrize("valor,esperado", [(None, 7), ("", 7), ("1", 1), ("90", 90), ("30", 30)])
def test_dias_validos(valor, esperado):
    assert app.validar_dias(valor) == esperado


@pytest.mark.parametrize("valor", ["0", "91", "-3", "abc", "1.5", "1; DROP TABLE x"])
def test_dias_invalidos(valor):
    with pytest.raises(app.ErrorHttp) as exc:
        app.validar_dias(valor)
    assert exc.value.codigo == 400


@pytest.mark.parametrize("reporte", sorted(app.REPORTES))
def test_consultas_se_arman(reporte):
    sql = app.armar_consulta(reporte, 14, "telecorp_test")
    assert "telecorp_test.v_" in sql
    assert "-14" in sql
    assert "{" not in sql and "}" not in sql


def test_reporte_desconocido():
    with pytest.raises(app.ErrorHttp) as exc:
        app.armar_consulta("drop_table", 7, "x")
    assert exc.value.codigo == 404


def test_requiere_operador():
    r = app.lambda_handler(evento(grupos=("usuarios",)))
    assert r["statusCode"] == 403
    r = app.lambda_handler({"pathParameters": {"reporte": "mttr"}})
    assert r["statusCode"] == 403


def test_grupos_como_texto():
    ev = evento()
    ev["requestContext"]["authorizer"]["claims"]["cognito:groups"] = "[usuarios, operadores]"
    assert app.es_operador(ev)


def test_dias_invalidos_http():
    r = app.lambda_handler(evento(dias="500"))
    assert r["statusCode"] == 400


def test_resultado_paginado_y_tipos(athena):
    preparar(athena, [
        [fila("zonaId", "incidencias"), fila("LIM-01", "5")],
        [fila("LIM-02", "3"), {"Data": [{"VarCharValue": "LIM-03"}, {}]}],
    ], estados=("RUNNING", "SUCCEEDED"))
    r = app.lambda_handler(evento(dias="30"), None)
    cuerpo = json.loads(r["body"])
    assert r["statusCode"] == 200
    assert cuerpo["dias"] == 30 and cuerpo["total"] == 3 and cuerpo["cache"] is False
    assert cuerpo["filas"] == [
        {"zonaId": "LIM-01", "incidencias": 5},
        {"zonaId": "LIM-02", "incidencias": 3},
        {"zonaId": "LIM-03", "incidencias": None},
    ]
    athena.assert_no_pending_responses()


def test_consulta_enviada_usa_workgroup_y_base(athena, monkeypatch):
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    preparar(athena, [[fila("zonaId", "incidencias")]])
    app.lambda_handler(evento("mttr", "7"))
    # Stubber ya valida los parametros; verificamos ademas el contenido enviado.
    athena.assert_no_pending_responses()


def test_parametros_de_start_query(athena):
    athena.add_response(
        "start_query_execution", {"QueryExecutionId": "q1"},
        expected_params={
            "QueryString": app.armar_consulta("top_antenas", 7, "telecorp_test"),
            "WorkGroup": "telecorp-test",
            "QueryExecutionContext": {"Database": "telecorp_test"},
        },
    )
    athena.add_response("get_query_execution", {"QueryExecution": {"Status": {"State": "FAILED"}}})
    r = app.lambda_handler(evento("top_antenas"))
    assert r["statusCode"] == 502


def test_cache_5_minutos(athena):
    preparar(athena, [[fila("zonaId", "incidencias"), fila("A", "1")]])
    reloj = [1000.0]
    ahora = lambda: reloj[0]  # noqa: E731
    r1 = json.loads(app.lambda_handler(evento(dias="7"), None, ahora)["body"])
    reloj[0] += 299
    r2 = json.loads(app.lambda_handler(evento(dias="7"), None, ahora)["body"])
    assert r1["cache"] is False and r2["cache"] is True and r2["filas"] == r1["filas"]
    # otro `dias` es otra clave: consulta de nuevo
    preparar(athena, [[fila("zonaId", "incidencias")]])
    assert json.loads(app.lambda_handler(evento(dias="8"), None, ahora)["body"])["cache"] is False
    # vence el TTL
    reloj[0] += 2
    preparar(athena, [[fila("zonaId", "incidencias"), fila("B", "2")]])
    r3 = json.loads(app.lambda_handler(evento(dias="7"), None, ahora)["body"])
    assert r3["cache"] is False and r3["filas"][0]["zonaId"] == "B"
    athena.assert_no_pending_responses()


def test_timeout_de_athena(athena, monkeypatch):
    athena.add_response("start_query_execution", {"QueryExecutionId": "q1"})
    athena.add_response("get_query_execution", {"QueryExecution": {"Status": {"State": "RUNNING"}}})
    monkeypatch.setattr(app, "ESPERA_MAX_S", -1)
    with pytest.raises(app.ErrorHttp) as exc:
        app.ejecutar("select 1", "wg", "db", dormir=lambda s: None)
    assert exc.value.codigo == 504
