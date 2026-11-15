"""Guion del pitch: escenario normal, caída de una zona y 12 reportes en esa zona.

Imprime, paso a paso, qué se envía y qué debería ocurrir en la plataforma.
Ejemplo (sin tocar la nube): python simulador/demo.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time

import antenas as sim_antenas
import reportes as sim_reportes
from comun import (
    REGION_POR_DEFECTO,
    UMBRAL_USUARIOS_COLECTIVO,
    VENTANA_COLECTIVO_HORAS,
    ErrorSimulador,
    ahora_utc,
    antenas_de_zona,
    cargar_antenas,
    cargar_zonas,
    clasificar,
    exigir_zona,
    ruta_datos,
    stage_por_defecto,
)

ZONA_DEMO_POR_DEFECTO = "LIM-SJL-02"
USUARIOS_DEMO = 12
MINUTOS_DEMO = 10
TIPO_DEMO = "SIN_INTERNET"


def _titulo(numero: int, texto: str) -> None:
    print()
    print("=" * 72)
    print(f"PASO {numero}: {texto}")
    print("=" * 72)


def _esperado(lineas: list[str]) -> None:
    print("  Lo que debería pasar:")
    for linea in lineas:
        print(f"   - {linea}")


def _tabla_antenas(lecturas: list[dict], antenas: list[dict]) -> None:
    zonas_por_antena = {a["antenaId"]: a["zonaId"] for a in antenas}
    print(f"  {'antena':<20}{'zona':<13}{'GB/min':>8}  estado")
    for lectura in lecturas:
        print(
            f"  {lectura['antenaId']:<20}{zonas_por_antena[lectura['antenaId']]:<13}"
            f"{lectura['gbPorMinuto']:>8.1f}  {clasificar(lectura['gbPorMinuto'])}"
        )


def _pausa(segundos: float) -> None:
    if segundos > 0:
        time.sleep(segundos)


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guion de demo para el pitch de Red TeleCorp.")
    parser.add_argument("--destino", choices=("stdout", "kinesis", "iot", "sqs"), default="stdout",
                        help="Adónde enviar: kinesis/iot para telemetría, sqs para reportes; stdout por defecto.")
    parser.add_argument("--dry-run", action="store_true", help="No llama a la nube; solo muestra lo que enviaría.")
    parser.add_argument("--zona", default=ZONA_DEMO_POR_DEFECTO, help="Zona que cae en el paso 2 y recibe reportes.")
    parser.add_argument("--stage", default=stage_por_defecto(), help="Stage de despliegue.")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", REGION_POR_DEFECTO), help="Región AWS.")
    parser.add_argument("--iot-endpoint", help="Endpoint de IoT Data (destino iot, opcional).")
    parser.add_argument("--datos", help="Carpeta con zonas.json y antenas.json.")
    parser.add_argument("--semilla", type=int, default=7, help="Semilla para repetir la demo.")
    parser.add_argument("--pausa", type=float, default=0.0, help="Segundos de espera entre pasos.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    try:
        return _ejecutar(args)
    except ErrorSimulador as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001  (CLI: muestra el fallo de la nube sin traceback)
        print(f"error de ejecución: {e}", file=sys.stderr)
        return 1


def _ejecutar(args: argparse.Namespace) -> int:
    datos = ruta_datos(args.datos)
    zonas = cargar_zonas(datos)
    antenas = cargar_antenas(datos)
    zona = exigir_zona(zonas, args.zona)
    if not antenas_de_zona(antenas, zona["zonaId"]):
        raise ErrorSimulador(f"la zona {zona['zonaId']} no tiene antenas en los datos")

    destino_telemetria = args.destino if args.destino in ("kinesis", "iot") else "stdout"
    destino_reportes = "sqs" if args.destino == "sqs" else "stdout"
    modo = "dry-run" if args.dry_run else "envío real"
    rng = random.Random(args.semilla)
    publicar = dict(stage=args.stage, region=args.region, iot_endpoint=args.iot_endpoint, dry_run=args.dry_run)

    print(f"DEMO RED TELECORP · zona de la demo: {zona['zonaId']} ({zona['nombre']})")
    print(f"Telemetría -> {destino_telemetria} · Reportes -> {destino_reportes} · modo {modo}")

    # Paso 1: funcionamiento normal.
    _titulo(1, "Funcionamiento normal (60 a 129 GB/min en todas las antenas)")
    lecturas = sim_antenas.generar_lecturas(antenas, "normal", set(), 0, 1, rng, ahora_utc())
    _tabla_antenas(lecturas, antenas)
    sim_antenas.publicar_lecturas(lecturas, destino_telemetria, **publicar)
    _esperado([
        "Todas las antenas quedan NORMAL; ninguna dispara incidencia.",
        "Las lecturas llegan por IoT (telecorp/antenas/+/trafico) y pasan a Kinesis telecorp-<stage>-telemetria.",
    ])
    _pausa(args.pausa)

    # Paso 2: caída de la zona.
    _titulo(2, f"Caída de la zona {zona['zonaId']} (antenas por debajo de 60 GB/min)")
    afectadas = sim_antenas.calcular_afectadas("caida", antenas, zona["zonaId"], None)
    lecturas = sim_antenas.generar_lecturas(antenas, "caida", afectadas, 0, 1, rng, ahora_utc())
    _tabla_antenas(lecturas, antenas)
    sim_antenas.publicar_lecturas(lecturas, destino_telemetria, **publicar)
    _esperado([
        f"Las antenas de {zona['zonaId']} quedan CAIDA (menos de {sim_antenas.UMBRAL_CAIDA_GB} GB/min).",
        "El resto de zonas sigue NORMAL.",
        "La zona aparece como degradada en el estado de la red.",
    ])
    _pausa(args.pausa)

    # Paso 3: reportes de usuarios en la zona caída.
    _titulo(3, f"{USUARIOS_DEMO} usuarios reportan {TIPO_DEMO} en {zona['zonaId']} en {MINUTOS_DEMO} minutos")
    plan = sim_reportes.plan_masivo(zona, USUARIOS_DEMO, MINUTOS_DEMO, TIPO_DEMO, rng, ahora_utc())
    usuarios_distintos = len({it["usuarioId"] for it in plan})
    print(f"  Reportes generados: {len(plan)} · usuarios distintos: {usuarios_distintos}")
    enviados = sim_reportes.enviar_reportes(plan, destino_reportes, **{k: v for k, v in publicar.items()
                                                                     if k != "iot_endpoint"})
    print(f"  Reportes {'simulados' if args.dry_run else 'enviados'}: {enviados}")
    _esperado([
        "API Gateway responde 202 y envía cada reporte a SQS telecorp-<stage>-reportes (contrato sección 1).",
        "registrar-reporte crea 12 incidencias REGISTRADA; si se repite un mensaje, no se duplica.",
        f"Con {usuarios_distintos} usuarios distintos en la zona (umbral: {UMBRAL_USUARIOS_COLECTIVO} "
        f"en menos de {VENTANA_COLECTIVO_HORAS} h) debería abrirse un incidente colectivo.",
        "La verificación de infraestructura cruza el incidente colectivo con la caída de antenas de la zona.",
        "clasificar-ia asigna tipo y severidad a cada incidencia, o usa reglas locales si Vertex no responde.",
    ])

    print()
    print("Fin de la demo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
