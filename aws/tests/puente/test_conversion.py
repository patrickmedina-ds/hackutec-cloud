import base64
import copy
import json

import pytest
from apoyo_puente import EVENTOS
from conversion import EventoInvalido, a_cloudevent, atributos_pubsub, mensaje_pubsub


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


def test_atributos_pubsub_toman_zona_del_snapshot():
    ce = a_cloudevent(EVENTOS["Incidencia.CambioEstado"])
    assert atributos_pubsub(ce) == {
        "ce_type": "Incidencia.CambioEstado",
        "ce_id": "evt-inc-1",
        "zonaId": "LIM-MIR-01",
    }


def test_atributos_pubsub_sin_zona():
    ce = a_cloudevent(EVENTOS["Notificacion.Fallida"])
    assert atributos_pubsub(ce) == {"ce_type": "Notificacion.Fallida", "ce_id": "evt-not-1"}


def test_mensaje_pubsub_lleva_el_cloudevent_completo():
    ce = a_cloudevent(EVENTOS["Antena.CambioEstado"])
    mensaje = mensaje_pubsub(ce)
    assert json.loads(base64.b64decode(mensaje["data"])) == ce
    assert mensaje["attributes"]["zonaId"] == "LIM-MIR-01"


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
