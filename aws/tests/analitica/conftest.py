import os
import sys
from pathlib import Path

RAIZ_AWS = Path(__file__).resolve().parents[2]

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ["BASE_DATOS"] = "telecorp_test"
os.environ["WORKGROUP"] = "telecorp-test"

sys.path.insert(0, str(RAIZ_AWS / "src" / "api_analitica"))
sys.path.insert(0, str(RAIZ_AWS / "analitica"))
# Otros bloques también tienen un `app.py`: se descarta cualquier módulo ajeno ya cargado.
sys.modules.pop("app", None)
