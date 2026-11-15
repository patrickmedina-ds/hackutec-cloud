import pytest
from jsonschema import ValidationError

from conftest import (NotificadorFalso, antena, cloudevent, incidencia, notificacion,
                      validar_detail)
from telecorp_azure import estado_api
from telecorp_azure.notificador_acs import NotificadorACS, construir_html
from telecorp_azure.replicacion import (cargar_cloudevent, procesar_evento, recortar_sms)

INC = "Incidencia.CambioEstado"
ANT = "Antena.CambioEstado"
NOT = "Notificacion.Solicitada"


def test_esquema_rechaza_evento_invalido():
    with pytest.raises(ValidationError):
        validar_detail(INC, {"incidenciaId": "x"})


def test_upsert_incidencia_y_agregado(repo, notificador):
    procesar_evento(cloudevent(INC, incidencia("a", "REGISTRADA"), "e1"), repo, notificador)
    procesar_evento(cloudevent(INC, incidencia("b", "ATENDIDA", prioridad=80, colectivo="COL-1"), "e2"),
                    repo, notificador)
    procesar_evento(cloudevent(INC, incidencia("c", "EN_REVISION", prioridad=10), "e3"), repo, notificador)
    zona = repo.zonas["LIM-MIR-01"]
    assert zona["abiertas"] == {"REGISTRADA": 1, "EN_REVISION": 1, "ATENDIDA": 1}
    assert zona["totalAbiertas"] == 3
    assert zona["colectivoActivo"] is True
    assert zona["incidenteColectivoId"] == "COL-1"
    assert zona["prioridadMax"] == 80
    assert zona["estado"] == "incidencia_identificada"
    assert repo.incidencias["b"]["estado"] == "ATENDIDA"


def test_transicion_actualiza_en_vez_de_duplicar(repo, notificador):
    procesar_evento(cloudevent(INC, incidencia("a", "REGISTRADA"), "e1"), repo, notificador)
    procesar_evento(cloudevent(INC, incidencia("a", "EN_REVISION", "REGISTRADA", "2026-11-15T14:05:00Z"), "e2"),
                    repo, notificador)
    zona = repo.zonas["LIM-MIR-01"]
    assert len(repo.incidencias) == 1
    assert zona["abiertas"] == {"REGISTRADA": 0, "EN_REVISION": 1, "ATENDIDA": 0}
    assert zona["estado"] == "en_evaluacion"


def test_restablecido_y_normal(repo, notificador):
    procesar_evento(cloudevent(INC, incidencia("a", "ATENDIDA"), "e1"), repo, notificador)
    procesar_evento(cloudevent(INC, incidencia("a", "SOLUCIONADA", "ATENDIDA", "2026-11-15T15:00:00Z"), "e2"),
                    repo, notificador)
    zona = repo.zonas["LIM-MIR-01"]
    assert zona["totalAbiertas"] == 0 and zona["colectivoActivo"] is False
    assert zona["prioridadMax"] is None
    assert zona["estado"] == "restablecido"
    procesar_evento(cloudevent(INC, incidencia("a", "CERRADA", "SOLUCIONADA", "2026-11-15T16:00:00Z"), "e3"),
                    repo, notificador)
    assert repo.zonas["LIM-MIR-01"]["estado"] == "normal"


def test_evento_fuera_de_orden_no_retrocede(repo, notificador):
    procesar_evento(cloudevent(INC, incidencia("a", "ATENDIDA", ts="2026-11-15T14:10:00Z"), "e1"),
                    repo, notificador)
    r = procesar_evento(cloudevent(INC, incidencia("a", "EN_REVISION", ts="2026-11-15T14:05:00Z"), "e2"),
                        repo, notificador)
    assert r["resultado"] == "incidencia_obsoleta"
    assert repo.incidencias["a"]["estado"] == "ATENDIDA"


def test_idempotente_por_ce_id(repo, notificador):
    ev = cloudevent(INC, incidencia("a", "REGISTRADA"), "e1")
    assert procesar_evento(ev, repo, notificador)["resultado"] == "incidencia_actualizada"
    antes = dict(repo.zonas["LIM-MIR-01"])
    assert procesar_evento(ev, repo, notificador)["resultado"] == "duplicado"
    assert repo.zonas["LIM-MIR-01"] == antes

    aviso = cloudevent(NOT, notificacion("SMS"), "n1")
    procesar_evento(aviso, repo, notificador)
    procesar_evento(aviso, repo, notificador)
    assert len(notificador.enviados) == 1


def test_antenas_degradadas(repo, notificador):
    procesar_evento(cloudevent(ANT, antena("A1", "CAIDA", "2026-11-15T14:00:00Z"), "e1"), repo, notificador)
    procesar_evento(cloudevent(ANT, antena("A2", "SATURADA", "2026-11-15T14:01:00Z"), "e2"), repo, notificador)
    zona = repo.zonas["LIM-MIR-01"]
    assert zona["antenasDegradadas"] == ["A1", "A2"]
    assert zona["estado"] == "incidencia_identificada"
    procesar_evento(cloudevent(ANT, antena("A1", "NORMAL", "2026-11-15T14:10:00Z"), "e3"), repo, notificador)
    procesar_evento(cloudevent(ANT, antena("A2", "NORMAL", "2026-11-15T14:11:00Z"), "e4"), repo, notificador)
    zona = repo.zonas["LIM-MIR-01"]
    assert zona["antenasDegradadas"] == []
    assert zona["estado"] == "normal"


def test_antena_no_pisa_agregado_de_incidencias_y_viceversa(repo, notificador):
    procesar_evento(cloudevent(ANT, antena("A1", "CAIDA"), "e1"), repo, notificador)
    procesar_evento(cloudevent(INC, incidencia("a", "REGISTRADA"), "e2"), repo, notificador)
    zona = repo.zonas["LIM-MIR-01"]
    assert zona["antenasDegradadas"] == ["A1"]
    assert zona["totalAbiertas"] == 1


def test_antena_evento_viejo_se_descarta(repo, notificador):
    procesar_evento(cloudevent(ANT, antena("A1", "NORMAL", "2026-11-15T14:10:00Z"), "e1"), repo, notificador)
    procesar_evento(cloudevent(ANT, antena("A1", "CAIDA", "2026-11-15T14:00:00Z"), "e2"), repo, notificador)
    assert repo.zonas["LIM-MIR-01"]["antenasDegradadas"] == []


def test_notificacion_sms_y_correo(repo, notificador):
    procesar_evento(cloudevent(NOT, notificacion("SMS", mensaje="x" * 300), "n1"), repo, notificador)
    procesar_evento(cloudevent(NOT, notificacion("EMAIL"), "n2"), repo, notificador)
    sms, correo = notificador.enviados
    assert sms[0] == "SMS" and sms[1] == "+51987654321" and sms[2] is None
    assert len(sms[3]) == 160
    assert correo[0] == "EMAIL" and correo[1] == "ana@example.com"
    assert correo[2].startswith("Red TeleCorp")
    assert repo.zonas == {}  # los avisos no tocan Cosmos DB


def test_notificacion_con_fallo_libera_marca_para_reintento(repo):
    ev = cloudevent(NOT, notificacion("EMAIL"), "n1")
    with pytest.raises(RuntimeError):
        procesar_evento(ev, repo, NotificadorFalso(falla=True))
    ok = NotificadorFalso()
    assert procesar_evento(ev, repo, ok)["resultado"] == "aviso_enviado"
    assert len(ok.enviados) == 1


def test_tipo_desconocido_se_ignora(repo, notificador):
    ev = {"id": "z1", "type": "Zona.UmbralSuperado", "data": {}}
    assert procesar_evento(ev, repo, notificador)["resultado"] == "ignorado"
    assert repo.eventos == set()


def test_cloudevent_sin_id_falla(repo, notificador):
    with pytest.raises(ValueError):
        procesar_evento({"type": INC, "data": {}}, repo, notificador)


def test_cargar_cloudevent_formatos():
    ev = {"id": "1", "type": INC, "data": {}}
    import json
    assert cargar_cloudevent(json.dumps(ev))["id"] == "1"
    assert cargar_cloudevent(json.dumps(ev).encode())["id"] == "1"
    assert cargar_cloudevent([ev])["id"] == "1"


def test_recortar_sms():
    assert recortar_sms("hola") == "hola"
    assert len(recortar_sms("a" * 161)) == 160


def test_api_estado_proyeccion(repo, notificador):
    procesar_evento(cloudevent(INC, incidencia("a", "EN_REVISION", zona="LIM-SMP-01"), "e1"), repo, notificador)
    procesar_evento(cloudevent(INC, incidencia("b", "ATENDIDA", zona="LIM-MIR-01"), "e2"), repo, notificador)
    lista = estado_api.listar(repo)
    assert [z["zonaId"] for z in lista["zonas"]] == ["LIM-MIR-01", "LIM-SMP-01"]
    assert lista["generadoEn"].endswith("Z")
    assert "antenas" not in lista["zonas"][0] and "_etag" not in lista["zonas"][0]
    assert estado_api.detalle(repo, "LIM-SMP-01")["estado"] == "en_evaluacion"
    assert estado_api.detalle(repo, "NO-EXISTE") is None


def test_sms_sin_remitente_se_da_por_resuelto(repo, caplog):
    n = NotificadorFalso(sms_habilitado=False)
    ev = cloudevent(NOT, notificacion("SMS"), "n1")
    with caplog.at_level("WARNING"):
        r = procesar_evento(ev, repo, n)
    assert r["resultado"] == "sms_omitido_sin_remitente"
    assert n.enviados == [] and "SMS omitido" in caplog.text
    assert procesar_evento(ev, repo, n)["resultado"] == "duplicado"  # no se reintenta


def test_html_del_correo():
    h = construir_html("Asunto <b>", "Línea 1\nLínea 2", "inc-1", "ATENDIDA")
    assert "Incidencia identificada" in h and "inc-1" in h and "Asunto &lt;b&gt;" in h
    assert "<br>" in h
    assert "En evaluación" in construir_html("a", "m", "i", "REGISTRADA")
    assert "Servicio restablecido" in construir_html("a", "m", "i", "SOLUCIONADA")


def test_acs_correo_incluye_html_y_texto(monkeypatch):
    import sys, types
    enviados = []

    class Poller:
        def result(self):
            return {"status": "Succeeded"}

    class EmailClient:
        def begin_send(self, msg):
            enviados.append(msg)
            return Poller()

    mod = types.ModuleType("azure.communication.email")
    mod.EmailClient = EmailClient
    monkeypatch.setitem(sys.modules, "azure.communication.email", mod)
    monkeypatch.setenv("ACS_EMAIL_REMITENTE", "no-responder@x.azurecomm.net")
    n = NotificadorACS()
    monkeypatch.setattr(n, "_cliente", lambda c: c())
    assert n.sms_habilitado is False
    n.enviar("EMAIL", "a@b.c", "Asunto", "Hola", incidencia_id="inc-1", estado="ATENDIDA")
    c = enviados[0]["content"]
    assert c["plainText"] == "Hola" and "Incidencia identificada" in c["html"]
    assert enviados[0]["senderAddress"] == "no-responder@x.azurecomm.net"
