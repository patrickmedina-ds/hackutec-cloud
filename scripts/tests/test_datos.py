"""Pruebas de los datos semilla: JSON válidos, IDs únicos y con formato, e integridad entre zonas y antenas."""

import json
import re

import pytest

from conftest import RAIZ

ZONAS_PATH = RAIZ / "datos" / "zonas.json"
ANTENAS_PATH = RAIZ / "datos" / "antenas.json"

TIPOS_ZONA = {"RESIDENCIAL", "COMERCIAL", "EDUCATIVA", "INDUSTRIAL", "MEDICA", "CRITICA"}
CAMPOS_ZONA = {"zonaId", "nombre", "tipoZona", "ciudad", "lat", "lon", "usuariosEstimados"}
CAMPOS_ANTENA = {"antenaId", "zonaId", "lat", "lon"}
RE_ZONA_ID = re.compile(r"^LIM-[A-Z]{3}-\d{2}$")
RE_ANTENA_ID = re.compile(r"^ANT-(LIM-[A-Z]{3}-\d{2})-(\d{2})$")


@pytest.fixture(scope="module")
def zonas():
    return json.loads(ZONAS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def antenas():
    return json.loads(ANTENAS_PATH.read_text(encoding="utf-8"))


def test_los_json_son_validos_y_son_listas(zonas, antenas):
    assert isinstance(zonas, list) and zonas
    assert isinstance(antenas, list) and antenas


def test_zonas_tienen_los_campos_requeridos(zonas):
    for zona in zonas:
        assert set(zona) == CAMPOS_ZONA, zona.get("zonaId")


def test_antenas_tienen_los_campos_requeridos(antenas):
    for antena in antenas:
        assert set(antena) == CAMPOS_ANTENA, antena.get("antenaId")


def test_hay_12_zonas(zonas):
    assert len(zonas) == 12


def test_zona_id_unico_y_con_formato(zonas):
    ids = [z["zonaId"] for z in zonas]
    assert len(ids) == len(set(ids)), "zonaId duplicados"
    for zona_id in ids:
        assert RE_ZONA_ID.match(zona_id), f"zonaId con formato incorrecto: {zona_id}"


def test_antena_id_unico_y_con_formato(antenas):
    ids = [a["antenaId"] for a in antenas]
    assert len(ids) == len(set(ids)), "antenaId duplicados"
    for antena in antenas:
        coincidencia = RE_ANTENA_ID.match(antena["antenaId"])
        assert coincidencia, f"antenaId con formato incorrecto: {antena['antenaId']}"
        # ANT-<zonaId>-NN: el prefijo debe ser el zonaId de la antena.
        assert coincidencia.group(1) == antena["zonaId"]


def test_cada_antena_apunta_a_una_zona_existente(zonas, antenas):
    zona_ids = {z["zonaId"] for z in zonas}
    for antena in antenas:
        assert antena["zonaId"] in zona_ids, f"{antena['antenaId']} apunta a zona inexistente"


def test_cada_zona_tiene_entre_3_y_4_antenas(zonas, antenas):
    for zona in zonas:
        cantidad = sum(1 for a in antenas if a["zonaId"] == zona["zonaId"])
        assert 3 <= cantidad <= 4, f"{zona['zonaId']} tiene {cantidad} antenas"


def test_estan_todos_los_tipo_zona(zonas):
    tipos = {z["tipoZona"] for z in zonas}
    assert tipos == TIPOS_ZONA


def test_zona_critica_es_el_aeropuerto(zonas):
    criticas = [z for z in zonas if z["tipoZona"] == "CRITICA"]
    assert len(criticas) == 1
    assert "Aeropuerto" in criticas[0]["nombre"]


def test_coordenadas_dentro_de_lima_y_callao(zonas, antenas):
    # Rango amplio que cubre Lima Metropolitana y el Callao.
    for punto in [*zonas, *antenas]:
        assert -12.4 <= punto["lat"] <= -11.7, punto
        assert -77.3 <= punto["lon"] <= -76.8, punto


def test_usuarios_estimados_son_enteros_positivos(zonas):
    for zona in zonas:
        assert isinstance(zona["usuariosEstimados"], int)
        assert zona["usuariosEstimados"] > 0


CLIENTES_PATH = RAIZ / "datos" / "clientes.json"
CAMPOS_CLIENTE = {
    "numeroServicio", "documento", "nombre", "telefono", "email", "zonaId", "plan", "estadoContrato", "creadoEn",
}


@pytest.fixture(scope="module")
def clientes():
    return json.loads(CLIENTES_PATH.read_text(encoding="utf-8"))


def test_clientes_con_formato(clientes, zonas):
    zona_ids = {z["zonaId"] for z in zonas}
    servicios = [c["numeroServicio"] for c in clientes]
    assert len(servicios) == len(set(servicios))
    for c in clientes:
        assert set(c) == CAMPOS_CLIENTE, c["numeroServicio"]
        assert re.fullmatch(r"SRV-\d{8}", c["numeroServicio"])
        assert re.fullmatch(r"\d{8}", c["documento"])
        assert re.fullmatch(r"\+519\d{8}", c["telefono"])
        assert c["email"].endswith("@example.com")
        assert c["zonaId"] in zona_ids
        assert c["estadoContrato"] in {"ACTIVO", "SUSPENDIDO"}


def test_cada_zona_tiene_al_menos_15_clientes_activos(clientes, zonas):
    for zona in zonas:
        activos = [c for c in clientes if c["zonaId"] == zona["zonaId"] and c["estadoContrato"] == "ACTIVO"]
        assert len(activos) >= 15, zona["zonaId"]
    assert any(c["estadoContrato"] == "SUSPENDIDO" for c in clientes)


def test_cliente_demo(clientes):
    demo = next(c for c in clientes if c["numeroServicio"] == "SRV-00000001")
    assert demo["documento"] == "70000001" and demo["zonaId"] == "LIM-JMA-01"
    assert demo["estadoContrato"] == "ACTIVO"
    assert sum(1 for c in clientes if c["documento"] == "70000001") == 1
