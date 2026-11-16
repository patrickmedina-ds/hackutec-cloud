"""Pruebas del simulador de reportes: cuerpo SQS del contrato, formatos peruanos, masivo y envío a SQS (moto)."""

import json
import random
import re
import uuid
from datetime import datetime, timedelta, UTC

import boto3
import pytest
from moto import mock_aws

import reportes as rep
from comun import ahora_utc

CAMPOS_REPORTE = {
    "documento", "numeroServicio", "telefono", "email", "zonaId", "lat", "lon",
    "fechaHoraEvento", "tipo", "descripcion",
}
CAMPOS_MENSAJE = {"incidenciaId", "usuarioId", "recibidoEn", "reporte"}
REGEX_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_cuerpo_sqs_tiene_los_campos_del_contrato(zonas, clientes):
    rng = random.Random(11)
    item = rep.plan_aislado(zonas, clientes, 1, rng, ahora_utc())[0]
    cuerpo = rep.cuerpo_sqs(item, recibido_en_ms=1_731_680_000_000)

    assert set(cuerpo) == CAMPOS_MENSAJE
    assert set(cuerpo["reporte"]) == CAMPOS_REPORTE
    assert uuid.UUID(cuerpo["incidenciaId"]).version == 4
    assert isinstance(cuerpo["usuarioId"], str) and cuerpo["usuarioId"]
    assert cuerpo["recibidoEn"] == 1_731_680_000_000
    assert REGEX_FECHA.match(cuerpo["reporte"]["fechaHoraEvento"])


def test_cuerpo_sqs_es_json_serializable(zonas, clientes):
    item = rep.plan_tormenta(zonas, clientes, 1, random.Random(2), ahora_utc())[0]
    assert json.loads(json.dumps(rep.cuerpo_sqs(item)))["reporte"]["zonaId"] == item["reporte"]["zonaId"]


def test_reportes_salen_de_contratos_activos(zonas, clientes):
    por_servicio = {c["numeroServicio"]: c for c in clientes}
    for item in rep.plan_tormenta(zonas, clientes, 300, random.Random(5), ahora_utc()):
        r = item["reporte"]
        contrato = por_servicio[r["numeroServicio"]]
        assert contrato["estadoContrato"] == "ACTIVO"
        assert item["usuarioId"] == f"cliente:{contrato['numeroServicio']}"
        for campo in ("documento", "telefono", "email", "zonaId"):
            assert r[campo] == contrato[campo]
        assert re.fullmatch(r"\d{8}", r["documento"])
        assert re.fullmatch(r"\+519\d{8}", r["telefono"])


def test_reporte_respeta_el_esquema_nuevo_reporte(zonas, clientes):
    rng = random.Random(21)
    for item in rep.plan_tormenta(zonas, clientes, 200, rng, ahora_utc()):
        reporte = item["reporte"]
        assert set(reporte) == CAMPOS_REPORTE
        assert reporte["tipo"] in rep.TIPOS
        assert 1 <= len(reporte["descripcion"]) <= 1000
        assert reporte["zonaId"] in {z["zonaId"] for z in zonas}
        zona = next(z for z in zonas if z["zonaId"] == reporte["zonaId"])
        assert abs(reporte["lat"] - zona["lat"]) <= 0.01
        assert abs(reporte["lon"] - zona["lon"]) <= 0.01


def test_masivo_genera_usuarios_distintos_en_la_misma_zona(zonas, clientes):
    zona = next(z for z in zonas if z["zonaId"] == "LIM-SJL-02")
    ahora = ahora_utc()
    plan = rep.plan_masivo(zona, clientes, 15, 10, "SIN_INTERNET", random.Random(99), ahora)

    assert len(plan) == 15
    assert len({it["usuarioId"] for it in plan}) == 15
    assert len({it["reporte"]["numeroServicio"] for it in plan}) == 15
    assert {it["reporte"]["zonaId"] for it in plan} == {"LIM-SJL-02"}
    assert {it["reporte"]["tipo"] for it in plan} == {"SIN_INTERNET"}

    limite = ahora - timedelta(minutes=10, seconds=5)
    for it in plan:
        instante = datetime.strptime(it["reporte"]["fechaHoraEvento"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        assert limite <= instante <= ahora + timedelta(seconds=5)


def test_masivo_con_doce_usuarios_supera_el_umbral_de_diez(zonas, clientes):
    zona = next(z for z in zonas if z["zonaId"] == "LIM-SJL-02")
    plan = rep.plan_masivo(zona, clientes, 12, 10, "SIN_INTERNET", random.Random(7), ahora_utc())
    assert len({it["usuarioId"] for it in plan}) >= 10


def test_lotes_de_diez():
    assert [len(l) for l in rep._lotes(list(range(25)), 10)] == [10, 10, 5]


def test_tormenta_envia_a_sqs_en_lotes_con_moto(zonas, clientes):
    with mock_aws():
        sqs = boto3.client("sqs", region_name="us-east-1")
        url = sqs.create_queue(QueueName="telecorp-dev-reportes")["QueueUrl"]
        plan = rep.plan_tormenta(zonas, clientes, 25, random.Random(4), ahora_utc())

        enviados = rep.enviar_reportes(plan, "sqs", stage="dev", region="us-east-1", cliente=sqs)
        assert enviados == 25

        cuerpos = []
        while len(cuerpos) < 25:
            mensajes = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10).get("Messages", [])
            assert mensajes, "la cola se quedó sin mensajes antes de tiempo"
            cuerpos.extend(json.loads(m["Body"]) for m in mensajes)

    assert len(cuerpos) == 25
    for cuerpo in cuerpos:
        assert set(cuerpo) == CAMPOS_MENSAJE
        assert set(cuerpo["reporte"]) == CAMPOS_REPORTE
    assert {c["incidenciaId"] for c in cuerpos} == {it["incidenciaId"] for it in plan}


def test_dry_run_no_crea_clientes_ni_envia(zonas, clientes, monkeypatch):
    def prohibido(*args, **kwargs):
        raise AssertionError("dry-run no debe llamar a la nube")

    monkeypatch.setattr(rep, "crear_cliente", prohibido)
    monkeypatch.setattr(rep.urllib.request, "urlopen", prohibido)

    plan = rep.plan_tormenta(zonas, clientes, 2000, random.Random(1), ahora_utc())
    assert rep.enviar_reportes(plan, "sqs", stage="dev", region="us-east-1", dry_run=True) == 2000
    assert rep.enviar_reportes(
        plan, "api", stage="dev", region="us-east-1", api_url="https://ejemplo.invalid/dev", dry_run=True,
    ) == 2000


def test_api_exige_url(zonas, clientes):
    plan = rep.plan_aislado(zonas, clientes, 1, random.Random(0), ahora_utc())
    with pytest.raises(rep.ErrorSimulador):
        rep.enviar_reportes(plan, "api", stage="dev", region="us-east-1")


def test_cli_masivo_exige_zona(capsys):
    assert rep.main(["--escenario", "masivo", "--dry-run"]) == 2
    assert "requiere --zona" in capsys.readouterr().err


def test_cli_masivo_stdout_imprime_quince_usuarios(capsys):
    codigo = rep.main(["--escenario", "masivo", "--zona", "LIM-MIR-01", "--usuarios", "15", "--semilla", "5"])
    lineas = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert codigo == 0
    cuerpos = [json.loads(l) for l in lineas]
    assert len({c["usuarioId"] for c in cuerpos}) == 15
    assert all(c["usuarioId"].startswith("cliente:SRV-") for c in cuerpos)
    assert all(c["reporte"]["zonaId"] == "LIM-MIR-01" for c in cuerpos)


def test_masivo_error_si_la_zona_no_tiene_suficientes_clientes(zonas, clientes):
    zona = next(z for z in zonas if z["zonaId"] == "LIM-SJL-02")
    with pytest.raises(rep.ErrorSimulador, match="clientes con contrato ACTIVO"):
        rep.plan_masivo(zona, clientes, 500, 10, "SIN_INTERNET", random.Random(1), ahora_utc())


def test_cuerpo_api_es_reporte_publico(zonas, clientes):
    item = rep.plan_aislado(zonas, clientes, 1, random.Random(3), ahora_utc())[0]
    cuerpo = rep.cuerpo_api(item)
    assert set(cuerpo) == {"documento", "numeroServicio", "tipo", "descripcion", "fechaHoraEvento"}
    assert rep.url_reportes("https://x/dev/") == "https://x/dev/reportes"
    assert rep.url_reportes("https://x/dev/reportes") == "https://x/dev/reportes"


def test_enviar_api_sin_token(zonas, clientes, monkeypatch):
    vistos = []

    class Resp:
        status = 202
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def falso(peticion, timeout):
        vistos.append(peticion)
        return Resp()

    monkeypatch.setattr(rep.urllib.request, "urlopen", falso)
    plan = rep.plan_aislado(zonas, clientes, 2, random.Random(3), ahora_utc())
    assert rep.enviar_reportes(plan, "api", stage="dev", region="us-east-1", api_url="https://x/dev") == 2
    assert vistos[0].full_url == "https://x/dev/reportes"
    assert vistos[0].get_header("Authorization") is None
