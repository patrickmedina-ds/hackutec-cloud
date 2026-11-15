"""Configuración común de las pruebas de datos y scripts."""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SCRIPTS = RAIZ / "scripts"

# Permite importar seed_dynamodb desde las pruebas sin instalarlo como paquete.
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
