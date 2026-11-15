"""Lambdas auxiliares de las Step Functions (pytest + moto)."""

from datetime import datetime, timedelta, timezone

import pytest

import clasificacion
import colectivo
import recurrencia
import transiciones
from telecorp_comun.estados import obtener, registrar_inicial, transicionar
from util import ulid_determinista


# ------------------------------------------------------------------ transiciones


@pytest.mark.parametrize(
    "actual,destino,esperado",
    [
        ("REGISTRADA", "EN_REVISION", ["EN_REVISION"]),
        ("REGISTRADA", "ATENDIDA", ["EN_REVISION", "ATENDIDA"]),
        ("REGISTRADA", "SOLUCIONADA", ["EN_REVISION", "SOLUCIONADA"]),
        ("EN_REVISION", "SOLUCIONADA", ["SOLUCIONADA"]),
        ("ATENDIDA", "EN_REVISION", []),  # nunca retrocede
        ("EN_REVISION", "EN_REVISION", []),  # nunca repite la misma transición
        ("CERRADA", "SOLUCIONADA", []),
        ("SOLUCIONADA", "ATENDIDA", []),
    ],
)
def test_camino_solo_avanza(actual, destino, esperado):
    assert [str(e) for e in transiciones.camino(actual, destino)] == esperado


def test_mover_recorre_pasos_validos_y_aplica_cambios(ddb, crear_reporte, leer_historial):
    crear_reporte("r1")
    r = transiciones.mover("r1", "ATENDIDA", "STEPFN:test", "x", {"incidenteColectivoId": "COL-1"}, cliente=ddb)
    assert r["pasos"] == ["EN_REVISION", "ATENDIDA"] and r["transicionado"]
    inc = obtener("r1", cliente=ddb)
    assert inc["estado"] == "ATENDIDA" and inc["incidenteColectivoId"] == "COL-1"
    assert leer_historial("r1") == ["REGISTRADA", "EN_REVISION", "ATENDIDA"]


def test_mover_ignora_invalidas_pero_guarda_atributos(ddb, crear_reporte, leer_historial):
    crear_reporte("r1")
    transicionar("r1", "EN_REVISION", "OPERADOR:x", cliente=ddb)
    transicionar("r1", "ATENDIDA", "OPERADOR:x", cliente=ddb)
    r = transiciones.mover("r1", "EN_REVISION", "STEPFN:test", None, {"incidenteColectivoId": "COL-9"}, cliente=ddb)
    assert not r["transicionado"] and r["motivo"] == "SIN_CAMINO"
    assert obtener("r1", cliente=ddb)["incidenteColectivoId"] == "COL-9"
    assert leer_historial("r1") == ["REGISTRADA", "EN_REVISION", "ATENDIDA"]


def test_mover_incidencia_inexistente(ddb):
    assert transiciones.mover("nada", "EN_REVISION", "STEPFN:test", cliente=ddb)["motivo"] == "NO_ENCONTRADA"


def test_mover_reintenta_ante_conflicto(ddb, crear_reporte, monkeypatch):
    crear_reporte("r1")
    original = transiciones.transicionar
    llamadas = {"n": 0}

    def con_conflicto(*args, **kwargs):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            # Otra ejecución gana la carrera justo antes.
            original("r1", "EN_REVISION", "OPERADOR:otro", cliente=ddb)
        return original(*args, **kwargs)

    monkeypatch.setattr(transiciones, "transicionar", con_conflicto)
    r = transiciones.mover("r1", "ATENDIDA", "STEPFN:test", cliente=ddb)
    assert r["estadoFinal"] == "ATENDIDA"


# ------------------------------------------------------------------ ciclo-vida


def test_clasificacion_pasa_a_en_revision_con_prioridad(ddb, crear_reporte, leer_historial):
    crear_reporte("r1")
    r = clasificacion.handler(
        {"incidenciaId": "r1", "tipoZona": "MEDICA", "ia": {"tipo": "SIN_INTERNET", "severidad": "ALTA", "resumen": "s"}},
        cliente=ddb,
    )
    inc = obtener("r1", cliente=ddb)
    assert inc["estado"] == "EN_REVISION"
    # 1 afectado: round((20·log10(2) + 10) · 2.0) = 32 → P3
    assert inc["prioridad"] == 32 and inc["nivelPrioridad"] == "P3" == r["nivelPrioridad"]
    assert inc["severidadIA"] == "ALTA" and inc["tipoIA"] == "SIN_INTERNET" and inc["resumenIA"] == "s"
    assert leer_historial("r1") == ["REGISTRADA", "EN_REVISION"]


def test_clasificacion_no_repite_transicion(ddb, crear_reporte, leer_historial):
    """Un evento REGISTRADA duplicado (o que llega tarde) no genera otra transición."""
    crear_reporte("r1")
    evento = {"incidenciaId": "r1", "tipoZona": "RESIDENCIAL", "ia": {"severidad": "rara"}}
    clasificacion.handler(evento, cliente=ddb)
    clasificacion.handler(evento, cliente=ddb)
    assert leer_historial("r1") == ["REGISTRADA", "EN_REVISION"]
    assert "severidadIA" not in obtener("r1", cliente=ddb)  # severidad inválida se descarta


def test_clasificacion_sigue_a_colectivo_atendido(ddb, crear_reporte, leer_historial):
    registrar_inicial({"incidenciaId": "ANT-1", "clase": "COLECTIVA", "origen": "ANTENA", "zonaId": "LIM-MIR-01"}, cliente=ddb)
    transiciones.mover("ANT-1", "ATENDIDA", "STEPFN:test", cliente=ddb)
    crear_reporte("r1", incidenteColectivoId="ANT-1")
    r = clasificacion.handler({"incidenciaId": "r1", "tipoZona": "MEDICA", "ia": {}}, cliente=ddb)
    assert r["estadoFinal"] == "ATENDIDA"
    assert leer_historial("r1") == ["REGISTRADA", "EN_REVISION", "ATENDIDA"]


# ------------------------------------------------------------------ colectivo


def test_crear_colectiva_con_antena_degradada_y_mover_hijos(ddb, crear_reporte, leer_historial):
    hijos = [crear_reporte(f"r{i}") for i in range(10)]
    transicionar("r0", "EN_REVISION", "OPERADOR:x", cliente=ddb)
    r = colectivo.handler(
        {
            "accion": "CREAR_COLECTIVA",
            "incidenteColectivoId": "COL-1",
            "zonaId": "LIM-MIR-01",
            "tipoZona": "MEDICA",
            "usuariosDistintos": 10,
            "incidencias": hijos,
            "antenas": [{"antenaId": "A1", "estado": "NORMAL"}, {"antenaId": "A2", "estado": "CAIDA"}],
            "ia": {"resumen": "Caída masiva", "severidad": "CRITICA", "causaProbable": "Antena A2"},
            "tipos": ["SIN_INTERNET", "SIN_INTERNET", "LENTITUD"],
        },
        cliente=ddb,
    )
    assert r["destino"] == "ATENDIDA" and r["antenaDegradada"]
    col = obtener("COL-1", cliente=ddb)
    assert col["clase"] == "COLECTIVA" and col["origen"] == "USUARIO" and col["estado"] == "ATENDIDA"
    assert col["antenasAfectadas"] == ["A2"] and col["tipo"] == "SIN_INTERNET" and col["nivelPrioridad"] == "P1"
    assert leer_historial("COL-1") == ["REGISTRADA", "EN_REVISION", "ATENDIDA"]

    for h in hijos:
        colectivo.handler(
            {"accion": "MOVER_HIJO", "incidenciaId": h, "destino": r["destino"], "incidenteColectivoId": "COL-1"},
            cliente=ddb,
        )
    assert all(obtener(h, cliente=ddb)["estado"] == "ATENDIDA" for h in hijos)
    assert all(obtener(h, cliente=ddb)["incidenteColectivoId"] == "COL-1" for h in hijos)


def test_crear_colectiva_sin_antena_queda_en_revision(ddb):
    r = colectivo.handler(
        {"accion": "CREAR_COLECTIVA", "incidenteColectivoId": "COL-2", "zonaId": "LIM-MIR-01",
         "usuariosDistintos": 12, "incidencias": [], "antenas": [{"antenaId": "A1", "estado": "NORMAL"}], "ia": {}},
        cliente=ddb,
    )
    assert r["estado"] == "EN_REVISION" and "antenasAfectadas" not in obtener("COL-2", cliente=ddb)


def _marcador(ddb, zona="LIM-MIR-01"):
    return ddb.get_item(
        TableName="telecorp-test-zona-ventana", Key={"zonaId": {"S": zona}, "sk": {"S": "COLECTIVO_ACTIVO"}}
    ).get("Item")


def test_crear_proactiva_reclama_marcador_y_es_idempotente(ddb, leer_historial):
    evento = {"accion": "CREAR_PROACTIVA", "antenaId": "A2", "zonaId": "LIM-MIR-01", "estadoAntena": "CAIDA",
              "ts": "2026-11-15T14:02:00Z", "eventoId": "ev-1"}
    r = colectivo.handler(evento, cliente=ddb)
    assert r["creada"] and r["incidenciaId"].startswith("ANT-") and len(r["incidenciaId"]) == 30
    inc = obtener(r["incidenciaId"], cliente=ddb)
    assert inc["clase"] == "COLECTIVA" and inc["origen"] == "ANTENA" and inc["estado"] == "ATENDIDA"
    assert inc["usuariosAfectados"] == 500 and inc["antenasAfectadas"] == ["A2"]
    assert _marcador(ddb)["incidenteColectivoId"]["S"] == r["incidenciaId"]

    # Reintento de la misma ejecución: mismo id, sin duplicar historial.
    assert colectivo.handler(evento, cliente=ddb)["incidenciaId"] == r["incidenciaId"]
    assert leer_historial(r["incidenciaId"]) == ["REGISTRADA", "EN_REVISION", "ATENDIDA"]

    # Otra antena (otro evento) encuentra el marcador: no crea otra incidencia.
    otra = colectivo.handler({**evento, "antenaId": "A3", "eventoId": "ev-2"}, cliente=ddb)
    assert otra == {"creada": False, "incidenciaId": r["incidenciaId"]}


def test_escalar_colectivo_en_revision(ddb, crear_reporte, leer_historial):
    colectivo.handler(
        {"accion": "CREAR_COLECTIVA", "incidenteColectivoId": "COL-3", "zonaId": "LIM-MIR-01",
         "usuariosDistintos": 10, "incidencias": [], "antenas": [], "ia": {"severidad": "MEDIA"}},
        cliente=ddb,
    )
    crear_reporte("r1", incidenteColectivoId="COL-3")
    r = colectivo.handler({"accion": "ESCALAR", "incidenciaId": "COL-3", "antenaId": "A2"}, cliente=ddb)
    assert r["escalada"] and r["hijos"] == ["r1"] and r["destino"] == "ATENDIDA"
    assert leer_historial("COL-3") == ["REGISTRADA", "EN_REVISION", "ATENDIDA"]
    # Segunda antena: ya está ATENDIDA, no hay nueva transición.
    assert not colectivo.handler({"accion": "ESCALAR", "incidenciaId": "COL-3", "antenaId": "A4"}, cliente=ddb)["escalada"]
    assert not colectivo.handler({"accion": "ESCALAR", "incidenciaId": "NO-EXISTE"}, cliente=ddb)["escalada"]


def test_restablecer_soluciona_y_libera_zona(ddb, crear_reporte, leer_historial):
    p = colectivo.handler(
        {"accion": "CREAR_PROACTIVA", "antenaId": "A2", "zonaId": "LIM-MIR-01", "estadoAntena": "INHABILITADA",
         "ts": "2026-11-15T14:02:00Z", "eventoId": "ev-9"},
        cliente=ddb,
    )
    crear_reporte("r1", incidenteColectivoId=p["incidenciaId"])
    r = colectivo.handler({"accion": "RESTABLECER", "incidenciaId": p["incidenciaId"], "zonaId": "LIM-MIR-01"}, cliente=ddb)
    assert r["restablecida"] and r["hijos"] == ["r1"]
    assert obtener(p["incidenciaId"], cliente=ddb)["estado"] == "SOLUCIONADA"
    assert _marcador(ddb) is None
    colectivo.handler({"accion": "MOVER_HIJO", "incidenciaId": "r1", "destino": "SOLUCIONADA",
                       "incidenteColectivoId": p["incidenciaId"]}, cliente=ddb)
    assert leer_historial("r1") == ["REGISTRADA", "EN_REVISION", "SOLUCIONADA"]


def test_restablecer_no_cierra_colectivo_sin_antenas(ddb):
    colectivo.handler(
        {"accion": "CREAR_COLECTIVA", "incidenteColectivoId": "COL-4", "zonaId": "LIM-MIR-01",
         "usuariosDistintos": 10, "incidencias": [], "antenas": [], "ia": {}},
        cliente=ddb,
    )
    r = colectivo.handler({"accion": "RESTABLECER", "incidenciaId": "COL-4", "zonaId": "LIM-MIR-01"}, cliente=ddb)
    assert not r["restablecida"] and obtener("COL-4", cliente=ddb)["estado"] == "EN_REVISION"


def test_accion_desconocida(ddb):
    with pytest.raises(ValueError):
        colectivo.handler({"accion": "OTRA"}, cliente=ddb)


def test_ulid_determinista():
    f = datetime(2026, 11, 15, 14, 2, tzinfo=timezone.utc)
    a, b = ulid_determinista(f, "x"), ulid_determinista(f, "x")
    assert a == b and len(a) == 26 and a != ulid_determinista(f, "y")
    assert ulid_determinista(f + timedelta(seconds=1), "x") > a  # ordenable por tiempo


# ------------------------------------------------------------------ recurrencia


@pytest.mark.parametrize(
    "serie,previo,esperado",
    [
        (["NORMAL", "CAIDA", "NORMAL", "SATURADA", "SATURADA", "NORMAL", "INHABILITADA"], None, 3),
        (["CAIDA", "NORMAL", "CAIDA"], None, 1),
        (["CAIDA", "NORMAL", "CAIDA"], "NORMAL", 2),
        ([], "NORMAL", 0),
    ],
)
def test_contar_episodios(serie, previo, esperado):
    assert recurrencia.contar_episodios(serie, previo) == esperado


def _metricas(ddb, antena, inicio, estados, paso_horas=6):
    for i, estado in enumerate(estados):
        ts = (inicio + timedelta(hours=i * paso_horas)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ddb.put_item(
            TableName="telecorp-test-metricas-antena",
            Item={"antenaId": {"S": antena}, "ts": {"S": ts}, "estado": {"S": estado},
                  "zonaId": {"S": "LIM-MIR-01"}, "gbPorMinuto": {"N": "80"}},
        )


def test_recurrencia_crea_incidencia_semanal_idempotente(ddb, leer_historial):
    ref = datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)  # 02:00 en Lima, lunes semana 47
    _metricas(ddb, "A2", ref - timedelta(days=6), ["NORMAL", "CAIDA", "NORMAL", "CAIDA", "NORMAL", "SATURADA", "NORMAL"])
    _metricas(ddb, "A9", ref - timedelta(days=6), ["NORMAL", "CAIDA", "NORMAL"])
    evento = {"antenaId": "A2", "zonaId": "LIM-MIR-01", "fechaReferencia": "2026-11-16T07:00:00Z"}

    r = recurrencia.handler(evento, cliente=ddb)
    assert r["creada"] and r["episodios"] == 3 and r["incidenciaId"] == "REC-A2-2026-W47"
    inc = obtener("REC-A2-2026-W47", cliente=ddb)
    assert inc["origen"] == "RECURRENCIA" and inc["clase"] == "COLECTIVA" and inc["estado"] == "EN_REVISION"
    assert inc["nivelPrioridad"] in ("P1", "P2") and inc["prioridad"] >= 45

    # Mismo día (reintento) u otro día de la misma semana: no duplica.
    assert not recurrencia.handler(evento, cliente=ddb)["creada"]
    assert not recurrencia.handler({**evento, "fechaReferencia": "2026-11-17T07:00:00Z"}, cliente=ddb)["creada"]
    assert leer_historial("REC-A2-2026-W47") == ["REGISTRADA", "EN_REVISION"]

    assert not recurrencia.handler({"antenaId": "A9", "zonaId": "LIM-MIR-01",
                                    "fechaReferencia": "2026-11-16T07:00:00Z"}, cliente=ddb)["creada"]


def test_recurrencia_prioridad_minima_p2(ddb):
    ddb.put_item(TableName="telecorp-test-zonas",
                 Item={"zonaId": {"S": "Z-CHICA"}, "tipoZona": {"S": "RESIDENCIAL"}, "usuariosEstimados": {"N": "1"}})
    ref = datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)
    _metricas(ddb, "A5", ref - timedelta(days=5), ["NORMAL", "CAIDA"] * 3, paso_horas=2)
    r = recurrencia.handler({"antenaId": "A5", "zonaId": "Z-CHICA", "fechaReferencia": iso(ref)}, cliente=ddb)
    assert r["nivelPrioridad"] == "P2" and obtener(r["incidenciaId"], cliente=ddb)["prioridad"] == 45


def test_id_recurrencia_usa_semana_de_lima():
    # 2026-11-16T04:30Z = domingo 23:30 en Lima (semana 46); una hora después ya es lunes (semana 47).
    assert recurrencia.id_recurrencia("A1", datetime(2026, 11, 16, 4, 30, tzinfo=timezone.utc)) == "REC-A1-2026-W46"
    assert recurrencia.id_recurrencia("A1", datetime(2026, 11, 16, 5, 30, tzinfo=timezone.utc)) == "REC-A1-2026-W47"


def iso(fecha):
    return fecha.strftime("%Y-%m-%dT%H:%M:%SZ")

