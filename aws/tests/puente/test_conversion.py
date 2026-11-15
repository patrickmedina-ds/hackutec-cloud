import copy
import json

import pytest
from apoyo_puente import EVENTOS
from conversion import EventoInvalido, a_cloudevent


@pytest.mark.parametrize("tipo", list(EVENTOS))
def test_cloudevent_cumple_contrato(tipo):
    evento = EVENTOS[tipo]
    ce = a_cloudevent(evento)
    assert ce["specversion"] == "1.0"
    assert ce["id"] == evento["id"]
    assert ce["source"] == f"telecorp/aws/{evento['source']}"
    assert ce["type"] == tipo
    assert ce["time"] == evento["time"]
    assert ce["datacontenttype"] == "application/json"
    assert ce["data"] == evento["detail"]


@pytest.mark.parametrize(
    "tipo,subject",
    [
        ("Incidencia.CambioEstado", "c7a1e0f2"),
        ("Zona.UmbralSuperado", "LIM-MIR-01"),
        ("Antena.CambioEstado", "LIM-MIR-01"),
        ("Notificacion.Fallida", "c7a1e0f2"),
    ],
)
def test_subject_es_zona_o_incidencia(tipo, subject):
    assert a_cloudevent(EVENTOS[tipo])["subject"] == subject


def test_evento_sin_detail_type_es_invalido():
    evento = copy.deepcopy(EVENTOS["Zona.UmbralSuperado"])
    del evento["detail-type"]
    with pytest.raises(EventoInvalido):
        a_cloudevent(evento)


def test_cloudevent_valida_contra_esquema_del_contrato():
    jsonschema = pytest.importorskip("jsonschema")
    from pathlib import Path

    esquema = json.loads((Path(__file__).resolve().parents[3] / "contratos" / "eventos.schema.json").read_text())
    for tipo, evento in EVENTOS.items():
        sub = {"$defs": esquema["$defs"], "$ref": f"#/$defs/{tipo}"}
        jsonschema.validate(a_cloudevent(evento)["data"], sub)
