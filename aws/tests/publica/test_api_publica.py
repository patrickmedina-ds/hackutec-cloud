"""API pública: reportar con DNI o número de servicio y consultar reportes, validando el contrato."""

import json
import uuid

PII = {"documento", "numeroServicio", "telefono", "email", "usuarioId", "lat", "lon", "nombre"}


def _evento(recurso, cuerpo):
    return {"resource": recurso, "httpMethod": "POST", "body": json.dumps(cuerpo)}


def _reportar(app, **cuerpo):
    base = {"tipo": "SIN_INTERNET", "descripcion": "Sin datos desde la mañana"}
    r = app.lambda_handler(_evento("/reportes", {**base, **cuerpo}), None)
    return r["statusCode"], json.loads(r["body"])


def _mensajes(sqs, url):
    msgs = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10).get("Messages", [])
    return [json.loads(m["Body"]) for m in msgs]


def test_cliente_valido_por_dni(entorno):
    app, _, sqs, url = entorno
    status, cuerpo = _reportar(app, documento="70000001")
    assert status == 202
    assert cuerpo["estado"] == "REGISTRADA"
    assert cuerpo["zonaId"] == "LIM-JMA-01" and cuerpo["zonaNombre"] == "Jesús María"
    uuid.UUID(cuerpo["incidenciaId"])
    [m] = _mensajes(sqs, url)
    assert set(m) == {"incidenciaId", "usuarioId", "recibidoEn", "reporte"}
    assert m["incidenciaId"] == cuerpo["incidenciaId"]
    assert m["usuarioId"] == "cliente:SRV-00000001"
    assert isinstance(m["recibidoEn"], int)
    assert m["reporte"]["numeroServicio"] == "SRV-00000001"
    assert m["reporte"]["fechaHoraEvento"].endswith("Z")


def test_cliente_valido_por_numero_de_servicio(entorno):
    app, _, sqs, url = entorno
    status, cuerpo = _reportar(app, numeroServicio="srv-00000013")
    assert status == 202 and cuerpo["zonaId"] == "LIM-MIR-01"
    [m] = _mensajes(sqs, url)
    assert m["reporte"]["documento"] == "70000002"


def test_zona_y_contacto_salen_del_contrato(entorno):
    app, _, sqs, url = entorno
    status, _ = _reportar(
        app, documento="70000001", zonaId="LIM-CAL-01", telefono="+51911111111", email="otro@x.com",
        usuarioId="hacker", lat=1.0, lon=2.0, fechaHoraEvento="2026-11-15T10:00:00Z",
    )
    assert status == 202
    [m] = _mensajes(sqs, url)
    rep = m["reporte"]
    assert rep["zonaId"] == "LIM-JMA-01"
    assert rep["telefono"] == "+51900000001" and rep["email"] == "c0001@example.com"
    assert "lat" not in rep and "lon" not in rep
    assert m["usuarioId"] == "cliente:SRV-00000001"
    assert set(rep) == {"documento", "numeroServicio", "telefono", "email", "zonaId", "fechaHoraEvento", "tipo", "descripcion"}
    assert rep["fechaHoraEvento"] == "2026-11-15T10:00:00Z"


def test_cliente_inexistente(entorno):
    app, _, sqs, url = entorno
    for datos in ({"documento": "12345678"}, {"numeroServicio": "SRV-99999999"}):
        status, cuerpo = _reportar(app, **datos)
        assert status == 404 and cuerpo["codigo"] == "CLIENTE_NO_ENCONTRADO"
        assert cuerpo["mensaje"] == "No encontramos un servicio con esos datos"
    # DNI y número que no corresponden al mismo contrato
    status, _ = _reportar(app, documento="70000002", numeroServicio="SRV-00000001")
    assert status == 404
    assert _mensajes(sqs, url) == []


def test_varios_servicios_y_eleccion_enmascarada(entorno):
    app, _, sqs, url = entorno
    status, cuerpo = _reportar(app, documento="70000002")
    assert status == 409 and cuerpo["codigo"] == "VARIOS_SERVICIOS"
    assert cuerpo["servicios"] == [
        {"numeroServicio": "SRV-****0002", "zonaId": "LIM-JMA-01", "zonaNombre": "Jesús María"},
        {"numeroServicio": "SRV-****0013", "zonaId": "LIM-MIR-01", "zonaNombre": "Miraflores"},
    ]
    assert _mensajes(sqs, url) == []
    status, cuerpo = _reportar(app, documento="70000002", numeroServicio="SRV-****0013")
    assert status == 202 and cuerpo["zonaId"] == "LIM-MIR-01"
    status, _ = _reportar(app, numeroServicio="SRV-****0013")
    assert status == 400


def test_contrato_suspendido(entorno):
    app, _, sqs, url = entorno
    status, cuerpo = _reportar(app, documento="70000004")
    assert status == 403 and cuerpo["codigo"] == "CONTRATO_SUSPENDIDO"
    assert _mensajes(sqs, url) == []


def test_validaciones(entorno):
    app, *_ = entorno
    assert _reportar(app)[0] == 400
    assert _reportar(app, documento="123")[0] == 400
    assert _reportar(app, documento="70000001", tipo="NADA")[0] == 400
    assert _reportar(app, documento="70000001", descripcion="")[0] == 400
    assert _reportar(app, documento="70000001", fechaHoraEvento="2999-01-01T00:00:00Z")[0] == 400
    r = app.lambda_handler({"resource": "/reportes", "httpMethod": "POST", "body": "no json"}, None)
    assert r["statusCode"] == 400


def test_post_incidencias_autenticado_usa_la_misma_validacion(entorno):
    app, *_ = entorno
    r = app.lambda_handler(_evento("/incidencias", {"documento": "12345678", "tipo": "OTRO", "descripcion": "x"}), None)
    assert r["statusCode"] == 404


def _sembrar_incidencia(ddb, inc_id, usuario, creado):
    ddb.put_item(
        TableName="telecorp-test-incidencias",
        Item={
            "incidenciaId": {"S": inc_id}, "usuarioId": {"S": usuario}, "creadoEn": {"S": creado},
            "estado": {"S": "EN_REVISION"}, "zonaId": {"S": "LIM-JMA-01"}, "tipo": {"S": "SIN_INTERNET"},
            "descripcion": {"S": "sin datos"}, "documento": {"S": "70000001"}, "numeroServicio": {"S": "SRV-00000001"},
            "telefono": {"S": "+51900000001"}, "email": {"S": "c0001@example.com"}, "lat": {"N": "-12.1"},
            "clase": {"S": "INDIVIDUAL"}, "origen": {"S": "USUARIO"}, "fechaHoraEvento": {"S": creado},
        },
    )
    for sk, anterior, nuevo, actor in (
        (f"{creado}#REGISTRADA", None, "REGISTRADA", "SISTEMA"),
        (f"{creado}x#EN_REVISION", "REGISTRADA", "EN_REVISION", "OPERADOR:sub-secreto"),
    ):
        item = {
            "incidenciaId": {"S": inc_id}, "sk": {"S": sk}, "estadoNuevo": {"S": nuevo},
            "actor": {"S": actor}, "ts": {"S": creado}, "comentario": {"S": "ok"},
        }
        if anterior:
            item["estadoAnterior"] = {"S": anterior}
        ddb.put_item(TableName="telecorp-test-historial-estados", Item=item)


def _consultar(app, **cuerpo):
    r = app.lambda_handler(_evento("/reportes/consulta", cuerpo), None)
    return r["statusCode"], json.loads(r["body"]), r["body"]


def test_consulta_devuelve_reportes_sin_pii(entorno):
    app, ddb, *_ = entorno
    _sembrar_incidencia(ddb, "inc-1", "cliente:SRV-00000001", "2026-11-15T10:00:00Z")
    _sembrar_incidencia(ddb, "inc-2", "cliente:SRV-00000001", "2026-11-15T11:00:00Z")
    _sembrar_incidencia(ddb, "inc-otro", "cliente:SRV-00000002", "2026-11-15T11:00:00Z")
    status, cuerpo, crudo = _consultar(app, documento="70000001", numeroServicio="SRV-00000001")
    assert status == 200
    assert cuerpo["servicio"] == "SRV-****0001" and cuerpo["zonaNombre"] == "Jesús María"
    assert [r["incidenciaId"] for r in cuerpo["reportes"]] == ["inc-2", "inc-1"]
    for r in cuerpo["reportes"]:
        assert not PII & set(r)
        assert [h["estadoNuevo"] for h in r["historial"]] == ["REGISTRADA", "EN_REVISION"]
        assert r["historial"][1]["actor"] == "OPERADOR"
    for secreto in ("70000001", "SRV-00000001", "+51900000001", "c0001@example.com", "sub-secreto"):
        assert secreto not in crudo


def test_consulta_con_datos_que_no_coinciden(entorno):
    app, *_ = entorno
    for datos in (
        {"documento": "70000002", "numeroServicio": "SRV-00000001"},
        {"documento": "70000001", "numeroServicio": "SRV-99999999"},
        {"documento": "abc", "numeroServicio": "SRV-00000001"},
    ):
        status, cuerpo, _ = _consultar(app, **datos)
        assert status == 404 and cuerpo["codigo"] == "CLIENTE_NO_ENCONTRADO"
    assert _consultar(app, documento="70000001")[0] == 400


def test_consulta_sin_reportes(entorno):
    app, *_ = entorno
    status, cuerpo, _ = _consultar(app, documento="70000002", numeroServicio="SRV-00000002")
    assert status == 200 and cuerpo["reportes"] == []
