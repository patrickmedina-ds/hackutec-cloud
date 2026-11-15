"""Las vistas solo pueden usar rutas del `detail` que existan en eventos.schema.json."""

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

RAIZ_AWS = Path(__file__).resolve().parents[2]
SQL_DIR = RAIZ_AWS / "analitica" / "sql"
ESQUEMA = RAIZ_AWS.parent / "contratos" / "eventos.schema.json"
STACK = RAIZ_AWS / "stacks" / "analitica.yaml"
ARCHIVOS = sorted(SQL_DIR.glob("*.sql"))

EVENTOS_POR_VISTA = {
    "v_transiciones": "Incidencia.CambioEstado",
    "v_antenas_eventos": "Antena.CambioEstado",
    "v_colectivos": "Zona.UmbralSuperado",
}


def rutas(esquema, nodo, prefijo=""):
    props = nodo.get("properties", {})
    salida = set()
    for nombre, sub in props.items():
        ruta = f"{prefijo}{nombre}"
        salida.add(ruta)
        if "$ref" in sub:
            sub = esquema["$defs"][sub["$ref"].split("/")[-1]]
        salida |= rutas(esquema, sub, ruta + ".")
    return salida


@pytest.fixture(scope="module")
def esquema():
    return json.loads(ESQUEMA.read_text())


def test_hay_las_diez_vistas():
    assert {p.stem for p in ARCHIVOS} == {
        "v_eventos", "v_transiciones", "v_incidencias", "v_antenas_eventos", "v_colectivos",
        "v_top_zonas", "v_top_antenas", "v_tipos_problema", "v_recurrencia", "v_mttr",
    }


@pytest.mark.parametrize("archivo", ARCHIVOS, ids=lambda p: p.stem)
def test_rutas_json_existen_en_el_esquema(archivo, esquema):
    sql = archivo.read_text()
    usadas = set(re.findall(r"\bdetalle\.([A-Za-z_][\w.]*)", sql))
    if archivo.stem in EVENTOS_POR_VISTA:
        validas = rutas(esquema, esquema["$defs"][EVENTOS_POR_VISTA[archivo.stem]])
        assert usadas <= validas, f"rutas inexistentes: {usadas - validas}"
        assert f"ce_type = '{EVENTOS_POR_VISTA[archivo.stem]}'" in sql
    else:
        assert not usadas or archivo.stem == "v_eventos"


@pytest.mark.parametrize("archivo", ARCHIVOS, ids=lambda p: p.stem)
def test_referencias_y_dialecto(archivo):
    sql = archivo.read_text()
    assert "{{db}}" in sql
    for prohibido in ("`", "JSON_VALUE", "QUALIFY", "SAFE_CAST", "TIMESTAMP_DIFF", "COUNTIF", "ANY_VALUE"):
        assert prohibido not in sql, prohibido
    for dep in re.findall(r"\{\{db\}\}\.(\w+)", sql):
        assert dep == "eventos" or (SQL_DIR / f"{dep}.sql").exists(), dep


def test_columnas_de_tabla_cubren_el_detail(esquema):
    texto = STACK.read_text()
    union = set()
    for nombre in ("Incidencia.CambioEstado", "Zona.UmbralSuperado", "Antena.CambioEstado"):
        union |= {r.split(".")[-1] for r in rutas(esquema, esquema["$defs"][nombre])}
    struct = re.search(r"struct<(.+)>\s*$", texto, re.M).group(0)
    for campo in union:
        assert f"{campo}:" in struct, campo
    # PII: nada de Notificacion.Solicitada en la tabla
    assert "destino" not in struct


class _Loader(yaml.SafeLoader):
    pass


def _etiqueta(loader, sufijo, nodo):
    return loader.construct_scalar(nodo) if isinstance(nodo, yaml.ScalarNode) else None


_Loader.add_multi_constructor("!", _etiqueta)


def test_named_queries_coinciden_con_los_sql():
    plantilla = yaml.load(STACK.read_text(), Loader=_Loader)
    consultas = {
        r["Properties"]["QueryString"]: n
        for n, r in plantilla["Resources"].items()
        if r["Type"] == "AWS::Athena::NamedQuery"
    }
    assert len(consultas) == len(ARCHIVOS)
    normalizar = lambda s: re.sub(r"\s+", " ", s).strip()  # noqa: E731
    textos = {normalizar(q) for q in consultas}
    for archivo in ARCHIVOS:
        esperado = f"CREATE OR REPLACE VIEW ${{BaseDatos}}.{archivo.stem} AS " + normalizar(
            archivo.read_text().replace("{{db}}", "${BaseDatos}")
        )
        assert esperado in textos, archivo.stem


def test_crear_vistas_ejecuta_en_orden():
    sys.path.insert(0, str(RAIZ_AWS / "analitica"))
    import crear_vistas

    assert set(crear_vistas.ORDEN) == {p.stem for p in ARCHIVOS}
    vistos = []
    for nombre, sql in crear_vistas.sentencias("telecorp_test"):
        assert sql.startswith(f"CREATE OR REPLACE VIEW telecorp_test.{nombre} AS")
        assert "{{db}}" not in sql
        for dep in re.findall(r"telecorp_test\.(v_\w+)", sql.split("AS\n", 1)[1]):
            assert dep in vistos, f"{nombre} usa {dep} antes de crearla"
        vistos.append(nombre)
