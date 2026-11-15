import json

import pytest
from conftest import antena, cloudevent

from telecorp_azure.respaldo import RespaldoBlob, ruta_blob


def _ce(ce_id="ce-1", ts="2026-11-15T14:02:00Z"):
    return cloudevent("Antena.CambioEstado", antena(ts=ts), ce_id)


def test_ruta_usa_fecha_tipo_e_id():
    assert ruta_blob(_ce()) == "2026/11/15/Antena.CambioEstado/ce-1.json"


def test_ruta_normaliza_zona_horaria_a_utc():
    ce = {**_ce(), "time": "2026-11-15T23:30:00-05:00"}
    assert ruta_blob(ce).startswith("2026/11/16/")


def test_ruta_sanea_segmentos():
    ce = {**_ce(ce_id="../a/b"), "type": "x/y z"}
    ruta = ruta_blob(ce)
    assert ruta.endswith("/x_y_z/.._a_b.json") and ruta.count("/") == 4


def test_ruta_sin_id_falla():
    with pytest.raises(ValueError):
        ruta_blob({"type": "T"})


def test_guarda_el_cloudevent_crudo(contenedor):
    ce = _ce()
    assert RespaldoBlob(contenedor).guardar(ce) is True
    assert json.loads(contenedor.blobs["2026/11/15/Antena.CambioEstado/ce-1.json"]) == ce
    assert contenedor.llamadas[0]["overwrite"] is False
    assert contenedor.llamadas[0]["content_type"] == "application/json"


def test_es_idempotente(contenedor):
    r = RespaldoBlob(contenedor)
    assert r.guardar(_ce()) is True
    original = dict(contenedor.blobs)
    assert r.guardar({**_ce(), "data": {"distinto": 1}}) is False
    assert contenedor.blobs == original


def test_error_de_almacenamiento_se_propaga(contenedor):
    contenedor.falla = RuntimeError("503")
    with pytest.raises(RuntimeError):
        RespaldoBlob(contenedor).guardar(_ce())


def test_replicar_respalda_y_procesa(contenedor, repo, notificador, monkeypatch):
    func = pytest.importorskip("azure.functions")
    if not hasattr(func, "FunctionApp"):
        pytest.skip("azure-functions no instalado")
    import function_app

    monkeypatch.setattr(function_app, "_respaldo", RespaldoBlob(contenedor))
    monkeypatch.setattr(function_app, "_repo", repo)
    monkeypatch.setattr(function_app, "_notificador", notificador)
    ce = _ce()
    evento = type("E", (), {"get_json": lambda self: ce})()
    fn = function_app.replicar
    for attr in ("_function", "_func"):  # el decorador envuelve la función original
        fn = getattr(fn, attr, fn)
    fn(evento)
    assert json.loads(contenedor.blobs["2026/11/15/Antena.CambioEstado/ce-1.json"]) == ce
    assert "ce-1" in repo.eventos
