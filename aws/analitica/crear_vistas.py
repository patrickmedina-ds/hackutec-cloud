"""Custom resource de CloudFormation: crea (CREATE OR REPLACE VIEW) las vistas de Athena.

Lee los .sql de `sql/` (junto a este archivo), reemplaza `{{db}}` por la base de Glue y los
ejecuta en orden de dependencias. En Delete borra las vistas (para que la base de Glue se
elimine limpia). Sin dependencias fuera de boto3 (incluido en el runtime de Lambda).
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import boto3

# Orden de dependencias: cada vista solo usa las anteriores.
ORDEN = (
    "v_eventos",
    "v_transiciones",
    "v_antenas_eventos",
    "v_colectivos",
    "v_incidencias",
    "v_top_zonas",
    "v_top_antenas",
    "v_tipos_problema",
    "v_recurrencia",
    "v_mttr",
)
DIR_SQL = Path(__file__).parent / "sql"
ESPERA_MAX_S = 60


def sentencias(db: str) -> list[tuple[str, str]]:
    salida = []
    for nombre in ORDEN:
        cuerpo = (DIR_SQL / f"{nombre}.sql").read_text(encoding="utf-8").replace("{{db}}", db).strip()
        salida.append((nombre, f"CREATE OR REPLACE VIEW {db}.{nombre} AS\n{cuerpo}"))
    return salida


def ejecutar(athena, sql: str, db: str, workgroup: str, dormir=time.sleep) -> None:
    qid = athena.start_query_execution(
        QueryString=sql, WorkGroup=workgroup, QueryExecutionContext={"Database": db}
    )["QueryExecutionId"]
    limite = time.monotonic() + ESPERA_MAX_S
    while True:
        estado = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        if estado["State"] == "SUCCEEDED":
            return
        if estado["State"] in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Athena {estado['State']}: {estado.get('StateChangeReason', '')}")
        if time.monotonic() > limite:
            raise TimeoutError(f"Athena no termino en {ESPERA_MAX_S}s ({qid})")
        dormir(1)


def aplicar(evento: dict, athena) -> None:
    props = evento["ResourceProperties"]
    db, wg = props["Database"], props["WorkGroup"]
    if evento["RequestType"] == "Delete":
        for nombre in reversed(ORDEN):
            try:
                ejecutar(athena, f"DROP VIEW IF EXISTS {db}.{nombre}", db, wg)
            except Exception as exc:  # noqa: BLE001 - borrar nunca debe bloquear el stack
                print(f"No se pudo borrar {nombre}: {exc}")
        return
    for _nombre, sql in sentencias(db):
        ejecutar(athena, sql, db, wg)


def _responder(evento: dict, estado: str, motivo: str = "") -> None:
    cuerpo = json.dumps({
        "Status": estado,
        "Reason": motivo[:900] or "OK",
        "PhysicalResourceId": evento.get("PhysicalResourceId") or f"vistas-{evento['ResourceProperties']['Database']}",
        "StackId": evento["StackId"],
        "RequestId": evento["RequestId"],
        "LogicalResourceId": evento["LogicalResourceId"],
        "Data": {},
    }).encode()
    req = urllib.request.Request(evento["ResponseURL"], data=cuerpo, method="PUT", headers={"Content-Type": ""})
    urllib.request.urlopen(req, timeout=15)  # noqa: S310 - URL prefirmada de CloudFormation


def handler(evento, _contexto):
    try:
        aplicar(evento, boto3.client("athena"))
        _responder(evento, "SUCCESS")
    except Exception as exc:  # noqa: BLE001
        print(f"Error creando vistas: {exc}")
        _responder(evento, "FAILED", str(exc))
