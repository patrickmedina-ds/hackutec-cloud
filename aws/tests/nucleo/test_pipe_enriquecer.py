"""pipe_enriquecer contra contratos/eventos.schema.json."""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from boto3.dynamodb.types import TypeSerializer
from jsonschema import Draft202012Validator

from conftest import RAIZ_AWS, cargar_modulo

ESQUEMA = json.loads((RAIZ_AWS.parent / "contratos" / "eventos.schema.json").read_text())
ser = TypeSerializer()
RE_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
ARN = "arn:aws:dynamodb:us-east-1:123456789012:table/telecorp-dev-{}/stream/2026-11-15T00:00:00.000"


@pytest.fixture(scope="module")
def app():
    return cargar_modulo("pipe_enriquecer")


def validar(tipo: str, detail: dict):
    esquema = {"$schema": ESQUEMA["$schema"], "$defs": ESQUEMA["$defs"], "$ref": f"#/$defs/{tipo}"}
    Draft202012Validator(esquema).validate(json.loads(json.dumps(detail)))  # debe ser JSON puro
    for campo in ("ts", "ventanaInicio", "ventanaFin"):
        if campo in detail:
            assert RE_FECHA.match(detail[campo]), campo


def imagen(d: dict) -> dict:
    return {k: ser.serialize(v) for k, v in d.items()}


def registro(tabla, nueva, vieja=None, evento="INSERT"):
    ddb = {"NewImage": imagen(nueva)}
    if vieja is not None:
        ddb["OldImage"] = imagen(vieja)
    return {"eventName": evento, "eventSourceARN": ARN.format(tabla), "dynamodb": ddb}


def test_cambio_estado_inicial_y_posterior(app):
    snapshot = {"zonaId": "LIM-MIR-01", "clase": "INDIVIDUAL", "origen": "USUARIO", "tipo": "SIN_INTERNET",
                "prioridad": Decimal("42"), "nivelPrioridad": "P3", "severidadIA": "ALTA"}
    inicial = registro("historial-estados", {
        "incidenciaId": "c7a1", "sk": "2026-11-15T14:03:00.000Z#REGISTRADA", "estadoNuevo": "REGISTRADA",
        "actor": "SISTEMA", "ts": "2026-11-15T14:03:00.000Z", "snapshot": snapshot,
    })
    posterior = registro("historial-estados", {
        "incidenciaId": "c7a1", "sk": "x", "estadoAnterior": "REGISTRADA", "estadoNuevo": "EN_REVISION",
        "actor": "STEPFN:ciclo-vida", "comentario": "ok", "ts": "2026-11-15T14:04:00.000Z",
        "snapshot": {**snapshot, "incidenteColectivoId": "COL-1"},
    })
    salida = app.lambda_handler([inicial, posterior], None)
    assert len(salida) == 2
    for d in salida:
        validar("Incidencia.CambioEstado", d)
    assert salida[0]["estadoAnterior"] is None and salida[0]["comentario"] is None
    assert salida[0]["snapshot"]["prioridad"] == 42 and isinstance(salida[0]["snapshot"]["prioridad"], int)
    assert salida[0]["snapshot"]["incidenteColectivoId"] is None
    assert salida[1]["estadoAnterior"] == "REGISTRADA" and salida[1]["snapshot"]["incidenteColectivoId"] == "COL-1"
    # solo las claves del contrato
    assert set(salida[0]) == {"incidenciaId", "estadoAnterior", "estadoNuevo", "actor", "comentario", "ts", "snapshot"}


def test_umbral_superado(app):
    marcador = registro("zona-ventana", {
        "zonaId": "LIM-MIR-01", "sk": "COLECTIVO_ACTIVO", "incidenteColectivoId": "COL-01JD",
        "usuariosDistintos": Decimal("10"), "ventanaInicio": "2026-11-14T15:00:00.000Z",
        "ventanaFin": "2026-11-15T14:03:00.000Z", "incidencias": ["a", "b"], "ttl": Decimal("1795000000"),
    })
    (d,) = app.lambda_handler([marcador], None)
    validar("Zona.UmbralSuperado", d)
    assert d["usuariosDistintos"] == 10 and d["incidencias"] == ["a", "b"] and "ttl" not in d and "sk" not in d


def test_cambio_estado_antena(app):
    nueva = {"antenaId": "ANT-LIM-MIR-01-03", "zonaId": "LIM-MIR-01", "estado": "CAIDA", "gbPorMinuto": Decimal("41.7"),
             "ultimaLectura": "2026-11-15T14:02:00Z", "cambioEstadoEn": "2026-11-15T14:02:00Z"}
    vieja = {**nueva, "estado": "NORMAL", "gbPorMinuto": Decimal("100")}
    (d,) = app.lambda_handler([registro("antenas", nueva, vieja, "MODIFY")], None)
    validar("Antena.CambioEstado", d)
    assert d == {"antenaId": "ANT-LIM-MIR-01-03", "zonaId": "LIM-MIR-01", "estadoAnterior": "NORMAL",
                 "estadoNuevo": "CAIDA", "gbPorMinuto": 41.7, "ts": "2026-11-15T14:02:00Z"}


def test_inhabilitada_con_cero_gb_cumple_el_esquema(app):
    nueva = {"antenaId": "A", "zonaId": "Z", "estado": "INHABILITADA", "gbPorMinuto": Decimal("0"),
             "cambioEstadoEn": "2026-11-15T14:02:00Z"}
    (d,) = app.lambda_handler([registro("antenas", nueva, {**nueva, "estado": "NORMAL"}, "MODIFY")], None)
    validar("Antena.CambioEstado", d)
    assert d["gbPorMinuto"] == 0


def test_stream_desconocido_falla_en_alto(app):
    with pytest.raises(ValueError):
        app.lambda_handler([registro("metricas-antena", {"antenaId": "A"})], None)


def test_valores_contra_el_modulo_real_de_la_capa(app):
    """Los tipos y fuentes del Pipe coinciden con telecorp_comun.eventos."""
    from telecorp_comun import eventos

    plantilla = (RAIZ_AWS / "template.yaml").read_text()
    for detail_type, source in eventos.SOURCE.items():
        if detail_type == eventos.NOTIFICACION_SOLICITADA:
            continue  # no sale de un Pipe
        assert f"DetailType: {detail_type}" in plantilla
        assert f"Source: {source}" in plantilla
