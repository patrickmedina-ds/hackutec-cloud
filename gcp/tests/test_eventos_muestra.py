"""Valida los CloudEvents de muestra contra el contrato y las rutas JSON de las vistas."""
import json
import re
from pathlib import Path

import jsonschema
import pytest

RAIZ = Path(__file__).resolve().parents[2]
MUESTRA = RAIZ / "gcp" / "ejemplos" / "eventos_muestra.jsonl"
ESQUEMA = RAIZ / "contratos" / "eventos.schema.json"
SQL = RAIZ / "gcp" / "sql"

TIPOS = ["Incidencia.CambioEstado", "Zona.UmbralSuperado", "Antena.CambioEstado", "Notificacion.Fallida"]


def _cargar():
    return [json.loads(l) for l in MUESTRA.read_text(encoding="utf-8").splitlines() if l.strip()]


def _esquema_de(tipo):
    esquema = json.loads(ESQUEMA.read_text(encoding="utf-8"))
    return {"$schema": esquema["$schema"], "$ref": f"#/$defs/{tipo}", "$defs": esquema["$defs"]}


def test_un_evento_por_tipo():
    assert sorted(e["type"] for e in _cargar()) == sorted(TIPOS)


@pytest.mark.parametrize("evento", _cargar(), ids=lambda e: e["type"])
def test_data_cumple_esquema(evento):
    jsonschema.validate(
        evento["data"], _esquema_de(evento["type"]), format_checker=jsonschema.FormatChecker()
    )


@pytest.mark.parametrize("evento", _cargar(), ids=lambda e: e["type"])
def test_sobre_cloudevent(evento):
    assert evento["specversion"] == "1.0"
    assert evento["datacontenttype"] == "application/json"
    for campo in ("id", "source", "type", "time", "subject"):
        assert evento[campo]


def _rutas(obj, prefijo="$"):
    """Todas las rutas JSON presentes en un objeto (incluye intermedias)."""
    rutas = {prefijo}
    if isinstance(obj, dict):
        for k, v in obj.items():
            rutas |= _rutas(v, f"{prefijo}.{k}")
    return rutas


def test_rutas_json_de_las_vistas_existen():
    """Cada ruta de las vistas debe existir en el sobre CloudEvent o en el detail de algun evento."""
    sobre, detalle = set(), set()
    for e in _cargar():
        sobre |= _rutas({k: v for k, v in e.items() if k != "data"})
        detalle |= _rutas(e["data"])
    # Las rutas del detalle pueden tener campos no nulos solo en algunos eventos; el esquema
    # cubre estadoAnterior/comentario aunque en la muestra sean null.
    detalle |= {"$.estadoAnterior", "$.comentario", "$.snapshot.incidenteColectivoId"}

    patron = re.compile(r"JSON_(?:VALUE|QUERY|VALUE_ARRAY)\((\w+),\s*'(\$[^']*)'\)")
    encontradas = 0
    for sql in SQL.glob("v_*.sql"):
        for base, ruta in patron.findall(sql.read_text(encoding="utf-8")):
            encontradas += 1
            if base == "data":  # sobre CloudEvent en eventos_raw
                assert ruta in sobre or ruta == "$.data", f"{sql.name}: {ruta}"
            elif base == "detalle":  # $.data de v_eventos
                assert ruta in detalle, f"{sql.name}: {ruta}"
    assert encontradas > 20


def test_sql_sin_marcadores_extranos():
    """Los SQL se pasan por templatefile: solo se permiten proyecto y dataset."""
    for sql in SQL.glob("*.sql"):
        usados = set(re.findall(r"\$\{(\w+)\}", sql.read_text(encoding="utf-8")))
        assert usados <= {"proyecto", "dataset"}, f"{sql.name}: {usados}"
