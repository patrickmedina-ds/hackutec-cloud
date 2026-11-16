"""Gestión del grupo `operadores`: GET/POST /operadores y DELETE /operadores/{email}."""

import json

import boto3
import pytest
from moto import mock_aws

from conftest import cargar_modulo
from test_api_incidencias import evento


@pytest.fixture
def pool():
    with mock_aws():
        idp = boto3.client("cognito-idp")
        pid = idp.create_user_pool(PoolName="p", UsernameAttributes=["email"])["UserPool"]["Id"]
        idp.create_group(UserPoolId=pid, GroupName="operadores")

        def usuario(email, operador=False):
            idp.admin_create_user(
                UserPoolId=pid, Username=email,
                UserAttributes=[{"Name": "email", "Value": email}],
            )
            if operador:
                idp.admin_add_user_to_group(UserPoolId=pid, Username=email, GroupName="operadores")

        usuario("ana@telecorp.pe", operador=True)
        usuario("beto@telecorp.pe", operador=True)
        usuario("cliente@correo.com")
        yield idp, pid, usuario


@pytest.fixture
def app(pool, monkeypatch):
    _, pid, _ = pool
    monkeypatch.setenv("USER_POOL_ID", pid)
    modulo = cargar_modulo("api_incidencias")
    modulo._cognito = None
    return modulo


def _claims(email="ana@telecorp.pe", grupos=("operadores",)):
    return {"sub": "sub-" + email, "grupos": list(grupos), "email": email}


def llamar(app, metodo, recurso="/operadores", body=None, ruta=None, email="ana@telecorp.pe", grupos=("operadores",)):
    ev = evento(recurso, metodo, sub="sub-" + email, grupos=list(grupos), ruta=ruta, body=body)
    ev["requestContext"]["authorizer"]["claims"]["email"] = email
    r = app.lambda_handler(ev, None)
    return r["statusCode"], json.loads(r["body"])


def miembros(pool):
    idp, pid, _ = pool
    return sorted(
        next(a["Value"] for a in u["Attributes"] if a["Name"] == "email")
        for u in idp.list_users_in_group(UserPoolId=pid, GroupName="operadores")["Users"]
    )


def test_listar_operadores(app):
    st, cuerpo = llamar(app, "GET")
    assert st == 200
    assert [o["email"] for o in cuerpo] == ["ana@telecorp.pe", "beto@telecorp.pe"]
    assert {"creado", "estado"} <= set(cuerpo[0])


def test_promover_usuario_existente(app, pool):
    st, cuerpo = llamar(app, "POST", body={"email": "Cliente@Correo.com"})
    assert st == 200 and cuerpo["email"] == "cliente@correo.com"
    assert "cliente@correo.com" in miembros(pool)


def test_promover_usuario_inexistente(app, pool):
    st, cuerpo = llamar(app, "POST", body={"email": "nadie@correo.com"})
    assert st == 404
    assert cuerpo["mensaje"] == "El usuario debe crear su cuenta primero"
    assert len(miembros(pool)) == 2


@pytest.mark.parametrize("body", [{"email": "no-es-correo"}, {}, {"email": 5}])
def test_promover_email_invalido(app, body):
    assert llamar(app, "POST", body=body)[0] == 400


def test_quitar_operador(app, pool):
    st, cuerpo = llamar(app, "DELETE", "/operadores/{email}", ruta={"email": "beto%40telecorp.pe"})
    assert st == 200 and cuerpo["email"] == "beto@telecorp.pe"
    assert miembros(pool) == ["ana@telecorp.pe"]


def test_no_puede_quitarse_a_si_mismo(app, pool):
    st, cuerpo = llamar(app, "DELETE", "/operadores/{email}", ruta={"email": "ana@telecorp.pe"})
    assert st == 409 and cuerpo["codigo"] == "AUTO_REVOCACION"
    assert len(miembros(pool)) == 2


def test_no_quitar_al_ultimo_operador(app, pool):
    idp, pid, _ = pool
    idp.admin_remove_user_from_group(UserPoolId=pid, Username="beto@telecorp.pe", GroupName="operadores")
    st, cuerpo = llamar(app, "DELETE", "/operadores/{email}", ruta={"email": "ana@telecorp.pe"}, email="otro@telecorp.pe")
    assert st == 409 and cuerpo["codigo"] == "ULTIMO_OPERADOR"
    assert miembros(pool) == ["ana@telecorp.pe"]


def test_quitar_quien_no_es_operador(app):
    st, _ = llamar(app, "DELETE", "/operadores/{email}", ruta={"email": "cliente@correo.com"})
    assert st == 404


@pytest.mark.parametrize(
    "metodo,recurso,ruta,body",
    [
        ("GET", "/operadores", None, None),
        ("POST", "/operadores", None, {"email": "cliente@correo.com"}),
        ("DELETE", "/operadores/{email}", {"email": "beto@telecorp.pe"}, None),
    ],
)
def test_clientes_reciben_403(app, pool, metodo, recurso, ruta, body):
    st, _ = llamar(app, metodo, recurso, body=body, ruta=ruta, email="cliente@correo.com", grupos=())
    assert st == 403
    assert miembros(pool) == ["ana@telecorp.pe", "beto@telecorp.pe"]
