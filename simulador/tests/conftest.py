"""Configuración de pytest: rutas del simulador y credenciales falsas para que nunca se use la nube real."""

import os
import sys
from pathlib import Path

import pytest

SIMULADOR_DIR = Path(__file__).resolve().parent.parent
if str(SIMULADOR_DIR) not in sys.path:
    sys.path.insert(0, str(SIMULADOR_DIR))

# Credenciales y región falsas: moto las usa y ninguna llamada llega a AWS real.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from comun import cargar_antenas, cargar_clientes, cargar_zonas, DATOS_EJEMPLO  # noqa: E402


@pytest.fixture
def datos_ejemplo():
    """Zonas y antenas de simulador/datos_ejemplo (3 zonas, 6 antenas)."""
    return cargar_zonas(DATOS_EJEMPLO), cargar_antenas(DATOS_EJEMPLO)


@pytest.fixture
def zonas(datos_ejemplo):
    return datos_ejemplo[0]


@pytest.fixture
def antenas(datos_ejemplo):
    return datos_ejemplo[1]


@pytest.fixture
def clientes():
    """Contratos de ejemplo de simulador/datos_ejemplo/clientes.json."""
    return cargar_clientes(DATOS_EJEMPLO)
