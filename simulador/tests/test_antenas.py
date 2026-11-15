"""Pruebas del simulador de antenas: rangos por escenario, carga útil y envío a Kinesis (moto)."""

import json
import random
from datetime import datetime, UTC

import boto3
import pytest
from moto import mock_aws

import antenas as sim
from comun import clasificar, UMBRAL_CAIDA_GB, UMBRAL_SATURACION_GB

CARGA_CONTRATO = {"antenaId", "zonaId", "ts", "gbPorMinuto"}
INSTANTE = datetime(2026, 11, 15, 14, 2, tzinfo=UTC)


def _estados_de_una_antena(escenario, total, afectada=True, seed=1):
    rng = random.Random(seed)
    return [
        clasificar(sim.generar_gb(escenario, afectada, paso, total, rng))
        for paso in range(total)
    ]


def test_umbrales_coinciden_con_telecorp_comun():
    assert UMBRAL_CAIDA_GB == 60
    assert UMBRAL_SATURACION_GB == 129
    assert clasificar(59.9) == "CAIDA"
    assert clasificar(60) == "NORMAL"
    assert clasificar(129) == "NORMAL"
    assert clasificar(129.1) == "SATURADA"
    assert clasificar(0) == "INHABILITADA"


def test_normal_queda_entre_60_y_129_y_clasifica_normal():
    rng = random.Random(42)
    for paso in range(500):
        gb = sim.generar_gb("normal", True, paso, 500, rng)
        assert 60 <= gb <= 129, gb
        assert clasificar(gb) == "NORMAL"


def test_normal_no_afectada_en_caida_sigue_normal():
    rng = random.Random(3)
    for paso in range(200):
        gb = sim.generar_gb("caida", False, paso, 200, rng)
        assert clasificar(gb) == "NORMAL"


def test_caida_queda_por_debajo_de_60_y_mayor_que_cero():
    rng = random.Random(7)
    for paso in range(500):
        gb = sim.generar_gb("caida", True, paso, 500, rng)
        assert 0 < gb < 60, gb
        assert clasificar(gb) == "CAIDA"


def test_saturacion_queda_por_encima_de_129():
    rng = random.Random(9)
    for paso in range(500):
        gb = sim.generar_gb("saturacion", True, paso, 500, rng)
        assert gb > 129, gb
        assert clasificar(gb) == "SATURADA"


def test_inhabilitada_es_cero():
    rng = random.Random(0)
    assert sim.generar_gb("inhabilitada", True, 0, 10, rng) == 0.0
    assert clasificar(sim.generar_gb("inhabilitada", True, 5, 10, rng)) == "INHABILITADA"


def test_recurrente_alterna_tres_caidas_y_tres_recuperaciones():
    estados = _estados_de_una_antena("recurrente", total=60)
    caidas = sum(1 for i, e in enumerate(estados) if e == "CAIDA" and (i == 0 or estados[i - 1] != "CAIDA"))
    recuperaciones = sum(
        1 for i, e in enumerate(estados) if e == "NORMAL" and i > 0 and estados[i - 1] == "CAIDA"
    )
    assert caidas == 3
    assert recuperaciones == 3
    assert set(estados) == {"CAIDA", "NORMAL"}


def test_recurrente_con_pocas_lecturas_no_falla():
    # Con 6 lecturas, cada fase ocupa exactamente un paso.
    estados = _estados_de_una_antena("recurrente", total=6)
    assert estados == ["CAIDA", "NORMAL", "CAIDA", "NORMAL", "CAIDA", "NORMAL"]


def test_lectura_tiene_la_carga_util_del_contrato(antenas):
    lecturas = sim.generar_lecturas(antenas, "normal", set(), 0, 1, random.Random(1), INSTANTE)
    assert len(lecturas) == len(antenas)
    for lectura, antena in zip(lecturas, antenas):
        assert set(lectura) == CARGA_CONTRATO
        assert lectura["antenaId"] == antena["antenaId"]
        assert lectura["zonaId"] == antena["zonaId"]
        assert lectura["ts"] == "2026-11-15T14:02:00Z"
        assert isinstance(lectura["gbPorMinuto"], float)


def test_caida_solo_afecta_las_antenas_de_la_zona(antenas):
    afectadas = sim.calcular_afectadas("caida", antenas, "LIM-MIR-01", None)
    assert afectadas == {"ANT-LIM-MIR-01-01", "ANT-LIM-MIR-01-02"}
    lecturas = sim.generar_lecturas(antenas, "caida", afectadas, 0, 1, random.Random(5), INSTANTE)
    for lectura in lecturas:
        esperado = "CAIDA" if lectura["zonaId"] == "LIM-MIR-01" else "NORMAL"
        assert clasificar(lectura["gbPorMinuto"]) == esperado


def test_inhabilitada_solo_afecta_la_antena_indicada(antenas):
    afectadas = sim.calcular_afectadas("inhabilitada", antenas, None, "ANT-LIM-SJL-02-01")
    lecturas = sim.generar_lecturas(antenas, "inhabilitada", afectadas, 0, 1, random.Random(5), INSTANTE)
    por_id = {l["antenaId"]: l["gbPorMinuto"] for l in lecturas}
    assert por_id["ANT-LIM-SJL-02-01"] == 0
    assert all(gb >= 60 for aid, gb in por_id.items() if aid != "ANT-LIM-SJL-02-01")


def test_topico_iot_sigue_el_contrato():
    assert sim.topico_iot("ANT-LIM-MIR-01-03") == "telecorp/antenas/ANT-LIM-MIR-01-03/trafico"


def test_publicar_kinesis_con_moto_usa_antena_como_clave(antenas):
    lecturas = sim.generar_lecturas(antenas, "normal", set(), 0, 1, random.Random(2), INSTANTE)
    with mock_aws():
        kinesis = boto3.client("kinesis", region_name="us-east-1")
        kinesis.create_stream(StreamName="telecorp-dev-telemetria", ShardCount=1)
        enviadas = sim.publicar_lecturas(
            lecturas, "kinesis", stage="dev", region="us-east-1", cliente=kinesis
        )
        assert enviadas == len(antenas)

        shard = kinesis.describe_stream(StreamName="telecorp-dev-telemetria")[
            "StreamDescription"
        ]["Shards"][0]["ShardId"]
        iterador = kinesis.get_shard_iterator(
            StreamName="telecorp-dev-telemetria", ShardId=shard, ShardIteratorType="TRIM_HORIZON"
        )["ShardIterator"]
        registros = kinesis.get_records(ShardIterator=iterador)["Records"]

    assert len(registros) == len(antenas)
    for registro, lectura in zip(registros, lecturas):
        assert registro["PartitionKey"] == lectura["antenaId"]
        assert json.loads(registro["Data"]) == lectura


class ClienteIotFalso:
    def __init__(self):
        self.publicaciones = []

    def publish(self, topic, qos, payload):
        self.publicaciones.append((topic, qos, json.loads(payload)))


def test_publicar_iot_usa_el_tema_del_contrato(antenas):
    lecturas = sim.generar_lecturas(antenas, "normal", set(), 0, 1, random.Random(4), INSTANTE)
    cliente = ClienteIotFalso()
    assert sim.publicar_lecturas(lecturas, "iot", stage="dev", region="us-east-1", cliente=cliente) == len(antenas)
    for (topic, qos, carga), lectura in zip(cliente.publicaciones, lecturas):
        assert topic == f"telecorp/antenas/{lectura['antenaId']}/trafico"
        assert qos == 1
        assert carga == lectura


def test_dry_run_no_crea_clientes_de_nube(antenas, monkeypatch):
    def prohibido(*args, **kwargs):
        raise AssertionError("dry-run no debe crear clientes de AWS")

    monkeypatch.setattr(sim, "crear_cliente", prohibido)
    lecturas = sim.generar_lecturas(antenas, "normal", set(), 0, 1, random.Random(8), INSTANTE)
    assert sim.publicar_lecturas(lecturas, "kinesis", stage="dev", region="us-east-1", dry_run=True) == len(antenas)
    assert sim.publicar_lecturas(lecturas, "iot", stage="dev", region="us-east-1", dry_run=True) == len(antenas)


def test_cli_caida_exige_zona(capsys):
    assert sim.main(["--escenario", "caida", "--dry-run"]) == 2
    assert "requiere --zona" in capsys.readouterr().err


def test_cli_recurrente_exige_pasos_suficientes(capsys):
    codigo = sim.main(
        ["--escenario", "recurrente", "--antena", "ANT-LIM-MIR-01-01", "--duracion", "10", "--intervalo", "5", "--dry-run"]
    )
    assert codigo == 2
    assert "al menos 6" in capsys.readouterr().err


def test_cli_dry_run_stdout_imprime_lecturas(capsys):
    codigo = sim.main(
        ["--escenario", "caida", "--zona", "LIM-SJL-02", "--duracion", "2", "--intervalo", "1", "--semilla", "3", "--dry-run"]
    )
    salida = capsys.readouterr().out.strip().splitlines()
    assert codigo == 0
    assert len(salida) == 2 * 6  # 2 pasos x 6 antenas
    caidas = [json.loads(l) for l in salida if json.loads(l)["zonaId"] == "LIM-SJL-02"]
    assert all(clasificar(l["gbPorMinuto"]) == "CAIDA" for l in caidas)
