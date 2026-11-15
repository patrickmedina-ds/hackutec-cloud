"""Utilidades de prueba del puente multinube (HTTP, credenciales y config falsas)."""

class Respuesta:
    def __init__(self, status_code=200, cuerpo=None, texto=""):
        self.status_code = status_code
        self._cuerpo = cuerpo or {}
        self.text = texto or str(self._cuerpo)

    def json(self):
        return self._cuerpo


class HttpFalso:
    """Sustituto de `requests.Session`: responde según el host y guarda las llamadas."""

    def __init__(self):
        self.llamadas = []
        self.respuestas = {}  # fragmento de URL -> Respuesta o Exception

    def responder(self, fragmento, respuesta):
        self.respuestas[fragmento] = respuesta

    def post(self, url, **kwargs):
        self.llamadas.append({"url": url, **kwargs})
        for fragmento, respuesta in self.respuestas.items():
            if fragmento in url:
                if isinstance(respuesta, Exception):
                    raise respuesta
                return respuesta
        raise AssertionError(f"URL no esperada: {url}")

    def a(self, fragmento):
        return [c for c in self.llamadas if fragmento in c["url"]]


class ConfigFalsa:
    def __init__(self, valores=None):
        self.valores = valores or {
            "azure_tenant_id": "tenant-123",
            "azure_client_id": "cliente-456",
            "azure_eventgrid_endpoint": "https://telecorp-test-eventos.eastus2-1.eventgrid.azure.net/api/events",
        }

    def obtener(self, clave):
        from configuracion import ConfiguracionFaltante

        if clave not in self.valores:
            raise ConfiguracionFaltante(clave)
        return self.valores[clave]


EVENTOS = {
    "Incidencia.CambioEstado": {
        "version": "0",
        "id": "evt-inc-1",
        "detail-type": "Incidencia.CambioEstado",
        "source": "telecorp.incidencias",
        "account": "123456789012",
        "time": "2026-11-15T14:03:00Z",
        "region": "us-east-1",
        "resources": [],
        "detail": {
            "incidenciaId": "c7a1e0f2",
            "estadoAnterior": "REGISTRADA",
            "estadoNuevo": "EN_REVISION",
            "actor": "STEPFN:ciclo-vida",
            "comentario": None,
            "ts": "2026-11-15T14:03:00Z",
            "snapshot": {"zonaId": "LIM-MIR-01", "clase": "INDIVIDUAL", "origen": "USUARIO"},
        },
    },
    "Zona.UmbralSuperado": {
        "id": "evt-zona-1",
        "detail-type": "Zona.UmbralSuperado",
        "source": "telecorp.zonas",
        "time": "2026-11-15T14:03:00Z",
        "detail": {
            "zonaId": "LIM-MIR-01",
            "incidenteColectivoId": "COL-01",
            "usuariosDistintos": 10,
            "ventanaInicio": "2026-11-14T15:00:00Z",
            "ventanaFin": "2026-11-15T14:03:00Z",
            "incidencias": ["c7a1e0f2"],
        },
    },
    "Antena.CambioEstado": {
        "id": "evt-ant-1",
        "detail-type": "Antena.CambioEstado",
        "source": "telecorp.antenas",
        "time": "2026-11-15T14:02:00Z",
        "detail": {
            "antenaId": "ANT-LIM-MIR-01-03",
            "zonaId": "LIM-MIR-01",
            "estadoAnterior": "NORMAL",
            "estadoNuevo": "CAIDA",
            "gbPorMinuto": 41.7,
            "ts": "2026-11-15T14:02:00Z",
        },
    },
    "Notificacion.Solicitada": {
        "id": "evt-not-1",
        "detail-type": "Notificacion.Solicitada",
        "source": "telecorp.notificaciones",
        "time": "2026-11-15T14:05:00Z",
        "detail": {
            "incidenciaId": "c7a1e0f2",
            "estado": "ATENDIDA",
            "canal": "SMS",
            "destino": "+51987654321",
            "asunto": "Red TeleCorp",
            "mensaje": "Identificamos una falla",
        },
    },
}
