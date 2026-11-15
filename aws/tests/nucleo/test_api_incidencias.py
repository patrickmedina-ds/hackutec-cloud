"""Autorización y rutas de api_incidencias."""

import json

import pytest

from conftest import cargar_modulo
from telecorp_comun import estados

ANA, BETO, OP = "sub-ana", "sub-beto", "sub-op"


@pytest.fixture
def app(ddb):
    modulo = cargar_modulo("api_incidencias")
    modulo._cliente = None
    for i, (inc, usuario, zona) in enumerate(
        [("a1", ANA, "LIM-MIR-01"), ("a2", ANA, "LIM-SAN-02"), ("b1", BETO, "LIM-MIR-01")]
    ):
        estados.registrar_inicial(
            {
                "incidenciaId": inc,
                "usuarioId": usuario,
                "clase": "INDIVIDUAL",
                "origen": "USUARIO",
                "zonaId": zona,
                "tipo": "SIN_INTERNET",
                "descripcion": "x",
                "documento": "12345678",  # PII: no debe salir por la API
                "creadoEn": f"2026-11-15T14:0{i}:00Z",
            },
            cliente=ddb,
        )
    ddb.put_item(
        TableName="telecorp-test-zonas",
        Item={"zonaId": {"S": "LIM-MIR-01"}, "nombre": {"S": "Miraflores"}, "tipoZona": {"S": "COMERCIAL"},
              "ciudad": {"S": "Lima"}, "lat": {"N": "-12.12"}, "lon": {"N": "-77.03"}, "usuariosEstimados": {"N": "9"}},
    )
    ddb.put_item(
        TableName="telecorp-test-antenas",
        Item={"antenaId": {"S": "ANT-1"}, "zonaId": {"S": "LIM-MIR-01"}, "estado": {"S": "CAIDA"}, "gbPorMinuto": {"N": "10.5"}},
    )
    return modulo


def evento(recurso, metodo, sub=None, grupos=None, ruta=None, query=None, body=None):
    claims = {}
    if sub:
        claims["sub"] = sub
    if grupos is not None:
        claims["cognito:groups"] = grupos
    return {
        "resource": recurso,
        "httpMethod": metodo,
        "pathParameters": ruta,
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"authorizer": {"claims": claims} if claims else None},
    }


def llamar(app, *args, **kw):
    r = app.lambda_handler(evento(*args, **kw), None)
    return r["statusCode"], json.loads(r["body"])


def ids(cuerpo):
    return sorted(i["incidenciaId"] for i in cuerpo["items"])


def test_usuario_solo_ve_las_suyas(app):
    codigo, cuerpo = llamar(app, "/incidencias", "GET", sub=ANA)
    assert codigo == 200 and ids(cuerpo) == ["a1", "a2"]
    assert all("documento" not in i and "usuarioId" not in i for i in cuerpo["items"])
    # los filtros no permiten salirse de lo propio
    codigo, cuerpo = llamar(app, "/incidencias", "GET", sub=ANA, query={"zonaId": "LIM-MIR-01"})
    assert ids(cuerpo) == ["a1"]


@pytest.mark.parametrize("grupos", ["operadores", "[operadores otros]", "otros,operadores", ["operadores"]])
def test_operador_ve_todo_con_cualquier_formato_de_grupos(app, grupos):
    codigo, cuerpo = llamar(app, "/incidencias", "GET", sub=OP, grupos=grupos)
    assert codigo == 200 and ids(cuerpo) == ["a1", "a2", "b1"]


def test_otro_grupo_no_es_operador(app):
    _, cuerpo = llamar(app, "/incidencias", "GET", sub=OP, grupos="operadores-falso")
    assert ids(cuerpo) == []


def test_operador_filtra_por_zona_estado_y_pagina(app):
    _, c = llamar(app, "/incidencias", "GET", sub=OP, grupos="operadores", query={"zonaId": "LIM-MIR-01"})
    assert ids(c) == ["a1", "b1"]
    _, c = llamar(app, "/incidencias", "GET", sub=OP, grupos="operadores", query={"estado": "REGISTRADA", "clase": "INDIVIDUAL"})
    assert ids(c) == ["a1", "a2", "b1"]
    codigo, c1 = llamar(app, "/incidencias", "GET", sub=OP, grupos="operadores", query={"limite": "2", "estado": "REGISTRADA"})
    assert len(c1["items"]) == 2 and c1["cursor"]
    _, c2 = llamar(app, "/incidencias", "GET", sub=OP, grupos="operadores", query={"limite": "2", "estado": "REGISTRADA", "cursor": c1["cursor"]})
    assert sorted(ids(c1) + ids(c2)) == ["a1", "a2", "b1"]


def test_validaciones_de_consulta(app):
    assert llamar(app, "/incidencias", "GET", sub=ANA, query={"limite": "x"})[0] == 400
    assert llamar(app, "/incidencias", "GET", sub=ANA, query={"cursor": "###"})[0] == 400
    assert llamar(app, "/incidencias", "GET", sub=OP, grupos="operadores", query={"estado": "NOPE"})[0] == 400


def test_detalle_ajeno_es_404_y_propio_200(app):
    assert llamar(app, "/incidencias/{incidenciaId}", "GET", sub=ANA, ruta={"incidenciaId": "b1"})[0] == 404
    codigo, c = llamar(app, "/incidencias/{incidenciaId}", "GET", sub=ANA, ruta={"incidenciaId": "a1"})
    assert codigo == 200 and c["incidenciaId"] == "a1" and "documento" not in c
    assert llamar(app, "/incidencias/{incidenciaId}", "GET", sub=OP, grupos="operadores", ruta={"incidenciaId": "b1"})[0] == 200
    assert llamar(app, "/incidencias/{incidenciaId}", "GET", sub=OP, grupos="operadores", ruta={"incidenciaId": "zz"})[0] == 404


def test_historial_respeta_propiedad(app):
    assert llamar(app, "/incidencias/{incidenciaId}/historial", "GET", sub=ANA, ruta={"incidenciaId": "b1"})[0] == 404
    codigo, h = llamar(app, "/incidencias/{incidenciaId}/historial", "GET", sub=ANA, ruta={"incidenciaId": "a1"})
    assert codigo == 200 and [x["estadoNuevo"] for x in h] == ["REGISTRADA"]


def test_patch_solo_operadores_y_con_409_en_transicion_invalida(app, ddb):
    ruta = {"incidenciaId": "a1"}
    r = "/incidencias/{incidenciaId}/estado"
    assert llamar(app, r, "PATCH", sub=ANA, ruta=ruta, body={"estadoNuevo": "EN_REVISION"})[0] == 403
    assert estados.obtener("a1", cliente=ddb)["estado"] == "REGISTRADA"

    codigo, c = llamar(app, r, "PATCH", sub=OP, grupos="operadores", ruta=ruta,
                       body={"estadoNuevo": "EN_REVISION", "comentario": "revisando"})
    assert codigo == 200 and c["estado"] == "EN_REVISION"

    # REGISTRADA -> SOLUCIONADA no existe; EN_REVISION -> REGISTRADA tampoco.
    codigo, c = llamar(app, r, "PATCH", sub=OP, grupos="operadores", ruta=ruta, body={"estadoNuevo": "REGISTRADA"})
    assert codigo == 409 and c["codigo"] == "TRANSICION_INVALIDA"
    codigo, _ = llamar(app, r, "PATCH", sub=OP, grupos="operadores", ruta={"incidenciaId": "a2"}, body={"estadoNuevo": "SOLUCIONADA"})
    assert codigo == 409

    _, h = llamar(app, "/incidencias/{incidenciaId}/historial", "GET", sub=OP, grupos="operadores", ruta=ruta)
    assert h[-1]["actor"] == f"OPERADOR:{OP}" and h[-1]["comentario"] == "revisando"
    # el usuario dueño ve el historial sin el sub del operador
    _, h = llamar(app, "/incidencias/{incidenciaId}/historial", "GET", sub=ANA, ruta=ruta)
    assert h[-1]["actor"] == "OPERADOR"


def test_patch_validaciones(app):
    r, ruta = "/incidencias/{incidenciaId}/estado", {"incidenciaId": "a1"}
    kw = dict(sub=OP, grupos="operadores", ruta=ruta)
    assert llamar(app, r, "PATCH", body={"estadoNuevo": "XX"}, **kw)[0] == 400
    assert llamar(app, r, "PATCH", body={"estadoNuevo": "EN_REVISION", "comentario": "x" * 501}, **kw)[0] == 400
    assert llamar(app, r, "PATCH", body={}, **kw)[0] == 400
    assert llamar(app, r, "PATCH", sub=OP, grupos="operadores", ruta={"incidenciaId": "zz"}, body={"estadoNuevo": "EN_REVISION"})[0] == 404


def test_zonas_es_publica_y_antenas_solo_operadores(app):
    codigo, zonas = llamar(app, "/zonas", "GET")  # sin identidad
    assert codigo == 200 and zonas == [
        {"zonaId": "LIM-MIR-01", "nombre": "Miraflores", "tipoZona": "COMERCIAL", "ciudad": "Lima", "lat": -12.12, "lon": -77.03}
    ]
    assert llamar(app, "/antenas", "GET", sub=ANA)[0] == 403
    codigo, a = llamar(app, "/antenas", "GET", sub=OP, grupos="operadores", query={"estado": "CAIDA"})
    assert codigo == 200 and a[0]["antenaId"] == "ANT-1" and a[0]["gbPorMinuto"] == 10.5
    assert llamar(app, "/antenas", "GET", sub=OP, grupos="operadores", query={"zonaId": "LIM-MIR-01"})[1][0]["antenaId"] == "ANT-1"
    assert llamar(app, "/antenas", "GET", sub=OP, grupos="operadores", query={"estado": "NORMAL"})[1] == []


def test_sin_identidad_en_ruta_protegida_es_401(app):
    assert llamar(app, "/incidencias", "GET")[0] == 401
    assert llamar(app, "/ruta-rara", "GET", sub=ANA)[0] == 404
