"""API de analitica para el panel del operador (consultas Athena predefinidas, sin SQL libre).

Ruta: GET /analitica/{reporte}?dias=N  (solo grupo `operadores` de Cognito).
Reportes: top_zonas, top_antenas, tipos_problema, mttr, colectivos, recurrencia.
Cache en memoria (por contenedor) de 5 minutos por (reporte, dias).
"""

from __future__ import annotations

import json
import logging
import os
import time

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

GRUPO_OPERADORES = "operadores"
DIAS_MIN, DIAS_MAX, DIAS_DEFECTO = 1, 90, 7
TTL_CACHE_S = 300
ESPERA_MAX_S = 24  # API Gateway corta a los 29 s
CABECERAS = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}

# Plantillas fijas; el unico valor interpolado es `dias`, ya validado como entero 1..90.
_VENTANA = "date_add('day', -{dias}, CAST(current_timestamp AS timestamp))"
REPORTES: dict[str, str] = {
    "top_zonas": f"""
        SELECT zonaId, count(*) AS incidencias,
               count_if(estado_actual NOT IN ('SOLUCIONADA', 'CERRADA')) AS abiertas,
               count_if(nivelPrioridad IN ('P1', 'P2')) AS prioridad_alta
        FROM {{db}}.v_incidencias
        WHERE registrada_en >= {_VENTANA}
        GROUP BY zonaId
        ORDER BY incidencias DESC
        LIMIT 20""",
    "top_antenas": f"""
        SELECT antenaId, arbitrary(zonaId) AS zonaId, count(*) AS degradaciones,
               count_if(estadoNuevo = 'CAIDA') AS caidas,
               count_if(estadoNuevo = 'SATURADA') AS saturaciones,
               count_if(estadoNuevo = 'INHABILITADA') AS inhabilitaciones,
               CAST(max(ts) AS varchar) AS ultima_degradacion
        FROM {{db}}.v_antenas_eventos
        WHERE degradada AND ts >= {_VENTANA}
        GROUP BY antenaId
        ORDER BY degradaciones DESC
        LIMIT 20""",
    "tipos_problema": f"""
        SELECT tipo, sum(incidencias) AS incidencias
        FROM {{db}}.v_tipos_problema
        WHERE dia >= CAST({_VENTANA} AS date)
        GROUP BY tipo
        ORDER BY incidencias DESC""",
    "mttr": f"""
        SELECT zonaId, nivelPrioridad, count(*) AS resueltas,
               round(avg(duracion_min), 1) AS mttr_min,
               approx_percentile(duracion_min, 0.5) AS mediana_min,
               max(duracion_min) AS maximo_min
        FROM {{db}}.v_incidencias
        WHERE duracion_min IS NOT NULL AND solucionada_en >= {_VENTANA}
        GROUP BY zonaId, nivelPrioridad
        ORDER BY mttr_min DESC
        LIMIT 100""",
    "colectivos": f"""
        SELECT incidenteColectivoId, zonaId, usuariosDistintos, totalIncidencias,
               CAST(detectado_en AS varchar) AS detectado_en
        FROM {{db}}.v_colectivos
        WHERE detectado_en >= {_VENTANA}
        ORDER BY detectado_en DESC
        LIMIT 100""",
    "recurrencia": f"""
        SELECT zonaId, count(DISTINCT incidenteColectivoId) AS colectivos,
               CAST(min(detectado_en) AS varchar) AS primer_colectivo,
               CAST(max(detectado_en) AS varchar) AS ultimo_colectivo,
               count(DISTINCT incidenteColectivoId) > 1 AS es_recurrente
        FROM {{db}}.v_colectivos
        WHERE detectado_en >= {_VENTANA}
        GROUP BY zonaId
        HAVING count(DISTINCT incidenteColectivoId) > 1
        ORDER BY colectivos DESC
        LIMIT 50""",
}

_athena = None
_cache: dict[tuple[str, int], tuple[float, dict]] = {}


class ErrorHttp(Exception):
    def __init__(self, codigo: int, mensaje: str):
        super().__init__(mensaje)
        self.codigo, self.mensaje = codigo, mensaje


def _cliente():
    global _athena
    if _athena is None:
        _athena = boto3.client("athena")
    return _athena


def es_operador(event: dict) -> bool:
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    grupos = claims.get("cognito:groups") or []
    if isinstance(grupos, str):  # API Gateway lo serializa como "[a, b]" o "a,b"
        grupos = [g.strip() for g in grupos.strip("[]").replace(",", " ").split()]
    return GRUPO_OPERADORES in grupos


def validar_dias(valor) -> int:
    if valor in (None, ""):
        return DIAS_DEFECTO
    try:
        dias = int(str(valor))
    except ValueError:
        raise ErrorHttp(400, f"dias debe ser un entero entre {DIAS_MIN} y {DIAS_MAX}") from None
    if not DIAS_MIN <= dias <= DIAS_MAX:
        raise ErrorHttp(400, f"dias debe estar entre {DIAS_MIN} y {DIAS_MAX}")
    return dias


def armar_consulta(reporte: str, dias: int, db: str) -> str:
    if reporte not in REPORTES:
        raise ErrorHttp(404, f"reporte desconocido; validos: {', '.join(sorted(REPORTES))}")
    return REPORTES[reporte].format(db=db, dias=dias).strip()


def _convertir(valor: str | None, tipo: str):
    if valor is None:
        return None
    if tipo in ("bigint", "integer", "smallint", "tinyint"):
        return int(valor)
    if tipo in ("double", "float", "real", "decimal"):
        return float(valor)
    if tipo == "boolean":
        return valor == "true"
    return valor


def ejecutar(sql: str, workgroup: str, db: str, dormir=time.sleep) -> list[dict]:
    athena = _cliente()
    qid = athena.start_query_execution(
        QueryString=sql, WorkGroup=workgroup, QueryExecutionContext={"Database": db}
    )["QueryExecutionId"]
    limite = time.monotonic() + ESPERA_MAX_S
    while True:
        estado = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        if estado["State"] == "SUCCEEDED":
            break
        if estado["State"] in ("FAILED", "CANCELLED"):
            log.error("Athena %s: %s", estado["State"], estado.get("StateChangeReason"))
            raise ErrorHttp(502, "la consulta de analitica fallo")
        if time.monotonic() > limite:
            raise ErrorHttp(504, "la consulta de analitica tardo demasiado")
        dormir(0.5)

    filas, columnas, primera = [], None, True
    for pagina in athena.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
        if columnas is None:
            info = pagina["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
            columnas = [(c["Label"], c["Type"]) for c in info]
        for fila in pagina["ResultSet"]["Rows"]:
            if primera:  # la primera fila de la primera pagina es el encabezado
                primera = False
                continue
            datos = fila["Data"]
            filas.append({
                nombre: _convertir(datos[i].get("VarCharValue") if i < len(datos) else None, tipo)
                for i, (nombre, tipo) in enumerate(columnas)
            })
    return filas


def _resp(codigo: int, cuerpo: dict) -> dict:
    return {"statusCode": codigo, "headers": CABECERAS, "body": json.dumps(cuerpo, ensure_ascii=False)}


def lambda_handler(event, _context=None, ahora=time.monotonic):
    try:
        if not es_operador(event):
            raise ErrorHttp(403, "solo operadores")
        reporte = ((event.get("pathParameters") or {}).get("reporte") or "").strip()
        dias = validar_dias((event.get("queryStringParameters") or {}).get("dias"))
        sql = armar_consulta(reporte, dias, os.environ["BASE_DATOS"])

        clave = (reporte, dias)
        guardado = _cache.get(clave)
        if guardado and ahora() - guardado[0] < TTL_CACHE_S:
            return _resp(200, {**guardado[1], "cache": True})

        filas = ejecutar(sql, os.environ["WORKGROUP"], os.environ["BASE_DATOS"])
        datos = {"reporte": reporte, "dias": dias, "total": len(filas), "filas": filas, "cache": False}
        _cache[clave] = (ahora(), datos)
        return _resp(200, datos)
    except ErrorHttp as exc:
        return _resp(exc.codigo, {"mensaje": exc.mensaje})
