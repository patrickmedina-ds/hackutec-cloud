"""Pruebas de sintaxis de los scripts bash."""

import shutil
import subprocess

import pytest

from conftest import SCRIPTS

BASH = shutil.which("bash")
SCRIPTS_BASH = ["deploy.sh", "destroy.sh"]


@pytest.mark.skipif(BASH is None, reason="bash no disponible")
@pytest.mark.parametrize("nombre", SCRIPTS_BASH)
def test_bash_n_sin_errores_de_sintaxis(nombre):
    resultado = subprocess.run(
        [BASH, "-n", str(SCRIPTS / nombre)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stderr


@pytest.mark.parametrize("nombre", SCRIPTS_BASH)
def test_script_usa_set_euo_pipefail(nombre):
    contenido = (SCRIPTS / nombre).read_text(encoding="utf-8")
    assert "set -euo pipefail" in contenido
