"""Ventana de 24 h y agrupación de registrar_reporte."""

import json
from datetime import timedelta

import pytest
from boto3.dynamodb.types import TypeDeserializer

from conftest import cargar_modulo
from telecorp_comun import estados

des = TypeDeserializer()
ZONA = "LIM-MIR-01"


@pytest.fixture
def app(ddb):
    modulo = cargar_modulo("registrar_reporte")
    modulo._cliente = None
    return modulo


def mensaje(n: int, usuario: str, zona: str = ZONA, **cambios) -> dict:
    reporte = {
        "documento": "12345678",
        "numeroServicio": "987654321",
        "zonaId": zona,
        "fechaHoraEvento": "2026-11-15T13:55:00Z",
        "tipo": "SIN_INTERNET",
        "descripcion": "Sin internet desde la mañana",
        **cambios,
    }
    return {
        "messageId": f"m{n}",
        "body": json.dumps({"incidenciaId": f"inc-{n}", "usuarioId": usuario, "recibidoEn": 1, "reporte": reporte}),
    }


def enviar(app, *registros):
    return app.lambda_handler({"Records": list(registros)}, None)


def marcadores(ddb, zona=ZONA):
    r = ddb.query(
        TableName="telecorp-test-zona-ventana",
        KeyConditionExpression="zonaId = :z AND sk = :s",
        ExpressionAttributeValues={":z": {"S": zona}, ":s": {"S": "COLECTIVO_ACTIVO"}},
    )
    return [{k: des.deserialize(v) for k, v in i.items()} for i in r["Items"]]


def incidencia(ddb, inc_id):
    return estados.obtener(inc_id, cliente=ddb)


def test_no_cuenta_usuarios_repetidos(app, ddb):
    # 9 usuarios distintos, cada uno reporta 3 veces: 27 reportes, solo 9 usuarios.
    n = 0
    for _ in range(3):
        for u in range(9):
            n += 1
            assert enviar(app, mensaje(n, f"u{u}")) == {"batchItemFailures": []}
    assert marcadores(ddb) == []


def test_dispara_exactamente_una_vez_en_el_decimo_usuario_y_agrupa_despues(app, ddb):
    for u in range(9):
        enviar(app, mensaje(u, f"u{u}"))
    assert marcadores(ddb) == []

    enviar(app, mensaje(9, "u9"))  # décimo usuario distinto
    m = marcadores(ddb)
    assert len(m) == 1
    assert m[0]["usuariosDistintos"] == 10
    assert m[0]["incidenteColectivoId"].startswith("COL-")
    assert len(m[0]["incidencias"]) == 10
    assert m[0]["ventanaInicio"] <= m[0]["ventanaFin"]
    colectivo = m[0]["incidenteColectivoId"]
    # el detonante no se agrupa solo: lo asocia la SFN
    assert "incidenteColectivoId" not in incidencia(ddb, "inc-9")

    # Reportes posteriores (incluso de usuarios nuevos y repetidos) se agrupan, sin nuevo marcador.
    enviar(app, mensaje(10, "u10"), mensaje(11, "u0"))
    assert [x["incidenteColectivoId"] for x in marcadores(ddb)] == [colectivo]
    for inc in ("inc-10", "inc-11"):
        assert incidencia(ddb, inc)["incidenteColectivoId"] == colectivo


def test_otra_zona_no_se_mezcla(app, ddb):
    for u in range(10):
        enviar(app, mensaje(u, f"u{u}"))
    enviar(app, mensaje(100, "x1", zona="LIM-SAN-02"))
    assert marcadores(ddb, "LIM-SAN-02") == []
    assert "incidenteColectivoId" not in incidencia(ddb, "inc-100")


def test_reportes_de_hace_mas_de_24h_no_cuentan(app, ddb):
    viejo = app.ahora() - timedelta(hours=25)
    for u in range(5):
        ddb.put_item(
            TableName="telecorp-test-zona-ventana",
            Item={
                "zonaId": {"S": ZONA},
                "sk": {"S": f"R#{app.iso(viejo)}#viejo{u}"},
                "incidenciaId": {"S": f"vieja-{u}"},
                "usuarioId": {"S": f"viejo{u}"},
            },
        )
    for u in range(9):  # 5 viejos + 9 nuevos = 14, pero solo 9 están en ventana
        enviar(app, mensaje(u, f"u{u}"))
    assert marcadores(ddb) == []
    enviar(app, mensaje(9, "u9"))
    assert len(marcadores(ddb)) == 1


def test_agrupa_al_mismo_estado_del_colectivo(app, ddb):
    for u in range(10):
        enviar(app, mensaje(u, f"u{u}"))
    colectivo = marcadores(ddb)[0]["incidenteColectivoId"]
    # La SFN crea el colectivo y lo lleva a ATENDIDA (REGISTRADA -> EN_REVISION -> ATENDIDA).
    estados.registrar_inicial(
        {"incidenciaId": colectivo, "clase": "COLECTIVA", "origen": "USUARIO", "zonaId": ZONA}, cliente=ddb
    )
    estados.transicionar(colectivo, "EN_REVISION", "STEPFN:colectivo", cliente=ddb)
    estados.transicionar(colectivo, "ATENDIDA", "STEPFN:colectivo", cliente=ddb)

    enviar(app, mensaje(20, "u20"))
    inc = incidencia(ddb, "inc-20")
    assert inc["estado"] == "ATENDIDA"
    assert inc["incidenteColectivoId"] == colectivo
    historial = ddb.query(
        TableName="telecorp-test-historial-estados",
        KeyConditionExpression="incidenciaId = :i",
        ExpressionAttributeValues={":i": {"S": "inc-20"}},
    )["Items"]
    assert [h["estadoNuevo"]["S"] for h in historial] == ["REGISTRADA", "EN_REVISION", "ATENDIDA"]


def test_mensaje_duplicado_es_idempotente(app, ddb):
    m = mensaje(1, "u1")
    assert enviar(app, m, m) == {"batchItemFailures": []}
    historial = ddb.query(
        TableName="telecorp-test-historial-estados",
        KeyConditionExpression="incidenciaId = :i",
        ExpressionAttributeValues={":i": {"S": "inc-1"}},
    )["Items"]
    assert len(historial) == 1


def test_reporta_fallos_parciales_del_lote(app, ddb):
    malo = mensaje(2, "u2", tipo="NO_EXISTE")
    sin_cuerpo = {"messageId": "m3", "body": "no es json"}
    r = enviar(app, mensaje(1, "u1"), malo, sin_cuerpo)
    assert r == {"batchItemFailures": [{"itemIdentifier": "m2"}, {"itemIdentifier": "m3"}]}
    assert incidencia(ddb, "inc-1")["clase"] == "INDIVIDUAL"


def test_incidencia_guardada_con_campos_del_contrato(app, ddb):
    enviar(app, mensaje(1, "u1", telefono="+51987654321", lat=-12.12, lon=-77.03))
    inc = incidencia(ddb, "inc-1")
    assert inc["usuarioId"] == "u1" and inc["origen"] == "USUARIO" and inc["estado"] == "REGISTRADA"
    assert inc["telefono"] == "+51987654321" and float(inc["lat"]) == -12.12
