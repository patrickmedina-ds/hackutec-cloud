import os
import sys
from pathlib import Path

import pytest

RAIZ_AWS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ_AWS / "src" / "puente_multinube"))
sys.path.insert(0, str(RAIZ_AWS / "layers" / "comun" / "python"))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ["STAGE"] = "test"


sys.path.insert(0, str(Path(__file__).resolve().parent))

# Otros bloques también tienen un `app.py`: se descarta cualquier módulo ajeno ya cargado.
for _modulo in ("app", "configuracion", "conversion", "destino_gcp", "destino_azure"):
    sys.modules.pop(_modulo, None)

from apoyo_puente import HttpFalso, Respuesta  # noqa: E402


@pytest.fixture
def http():
    h = HttpFalso()
    h.responder("pubsub.googleapis.com", Respuesta(200, {"messageIds": ["m-1"]}))
    h.responder("login.microsoftonline.com", Respuesta(200, {"access_token": "token-entra", "expires_in": 3599}))
    h.responder("eventgrid.azure.net", Respuesta(200))
    return h
