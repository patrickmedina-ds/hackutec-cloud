import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "azure" / "functions"))

ESQUEMA = json.loads((RAIZ / "contratos" / "eventos.schema.json").read_text(encoding="utf-8"))


def validar_detail(tipo: str, data: dict) -> None:
    """Valida `data` contra la definición del tipo en eventos.schema.json."""
    esquema = {"$schema": ESQUEMA["$schema"], "$defs": ESQUEMA["$defs"], "$ref": f"#/$defs/{tipo}"}
    Draft202012Validator(esquema, format_checker=FormatChecker()).validate(data)


class RepoFalso:
    """Cosmos DB en memoria con la interfaz de `Repositorio`."""

    def __init__(self):
        self.eventos: set[str] = set()
        self.incidencias: dict[str, dict] = {}
        self.zonas: dict[str, dict] = {}

    def reclamar_evento(self, ce_id):
        if ce_id in self.eventos:
            return False
        self.eventos.add(ce_id)
        return True

    def liberar_evento(self, ce_id):
        self.eventos.discard(ce_id)

    def obtener_incidencia(self, incidencia_id):
        return self.incidencias.get(incidencia_id)

    def guardar_incidencia(self, doc):
        self.incidencias[doc["id"]] = dict(doc)

    def listar_incidencias_zona(self, zona_id):
        return [d for d in self.incidencias.values() if d.get("zonaId") == zona_id]

    def modificar_zona(self, zona_id, fn):
        doc = self.zonas.get(zona_id) or {"id": zona_id, "zonaId": zona_id}
        self.zonas[zona_id] = fn(doc)
        return self.zonas[zona_id]

    def obtener_zona(self, zona_id):
        return self.zonas.get(zona_id)

    def listar_zonas(self):
        return list(self.zonas.values())


class NotificadorFalso:
    def __init__(self, falla=False, sms_habilitado=True):
        self.enviados = []
        self.falla = falla
        self.sms_habilitado = sms_habilitado

    def enviar(self, canal, destino, asunto, mensaje, incidencia_id=None, estado=None):
        if self.falla:
            raise RuntimeError("ACS caído")
        self.enviados.append((canal, destino, asunto, mensaje))


@pytest.fixture
def repo():
    return RepoFalso()


@pytest.fixture
def notificador():
    return NotificadorFalso()


def cloudevent(tipo: str, data: dict, ce_id: str) -> dict:
    """CloudEvent como lo arma puente-multinube (integraciones.md sección 3); valida `data`."""
    validar_detail(tipo, data)
    return {
        "specversion": "1.0",
        "id": ce_id,
        "source": "telecorp/aws/telecorp.test",
        "type": tipo,
        "time": data.get("ts", "2026-11-15T14:00:00Z"),
        "datacontenttype": "application/json",
        "subject": data.get("zonaId") or data.get("incidenciaId"),
        "data": data,
    }


def incidencia(inc_id="inc-1", nuevo="REGISTRADA", anterior=None, ts="2026-11-15T14:00:00Z",
               zona="LIM-MIR-01", prioridad=42, colectivo=None) -> dict:
    return {
        "incidenciaId": inc_id, "estadoAnterior": anterior, "estadoNuevo": nuevo,
        "actor": "STEPFN:ciclo-vida", "comentario": None, "ts": ts,
        "snapshot": {"zonaId": zona, "clase": "INDIVIDUAL", "origen": "USUARIO",
                     "tipo": "SIN_INTERNET", "prioridad": prioridad, "nivelPrioridad": "P3",
                     "severidadIA": "ALTA", "incidenteColectivoId": colectivo},
    }


def antena(antena_id="ANT-LIM-MIR-01-03", nuevo="CAIDA", ts="2026-11-15T14:02:00Z",
           zona="LIM-MIR-01") -> dict:
    return {"antenaId": antena_id, "zonaId": zona, "estadoAnterior": "NORMAL",
            "estadoNuevo": nuevo, "gbPorMinuto": 41.7, "ts": ts}


def notificacion(canal="SMS", **extra) -> dict:
    base = {"incidenciaId": "inc-1", "estado": "ATENDIDA", "canal": canal,
            "destino": "+51987654321" if canal == "SMS" else "ana@example.com",
            "asunto": "Red TeleCorp: incidencia identificada en tu zona",
            "mensaje": "Identificamos una falla en tu zona."}
    base.update(extra)
    return base


class ContenedorFalso:
    """ContainerClient en memoria: `upload_blob` respeta `overwrite=False`."""

    def __init__(self):
        self.blobs: dict[str, bytes] = {}
        self.llamadas: list[dict] = []
        self.falla: Exception | None = None

    def upload_blob(self, name, data, overwrite=False, **kwargs):
        from telecorp_azure.respaldo import ResourceExistsError

        self.llamadas.append({"name": name, "overwrite": overwrite, **kwargs})
        if self.falla:
            raise self.falla
        if name in self.blobs and not overwrite:
            raise ResourceExistsError("BlobAlreadyExists")
        self.blobs[name] = data


@pytest.fixture
def contenedor():
    return ContenedorFalso()
