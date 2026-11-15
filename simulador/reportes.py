"""Simulador de reportes de usuarios (contrato: contratos/integraciones.md sección 1 y openapi.yaml NuevoReporte).

Escenarios:
  aislado    reportes dispersos entre zonas y horas.
  masivo     muchos usuarios distintos en una misma zona (dispara el umbral colectivo).
  tormenta   ráfaga de reportes en varias zonas, enviada en lotes de 10.

Destinos: sqs (SendMessageBatch a telecorp-<stage>-reportes), api (POST a /incidencias) o stdout.
Ejemplo: python simulador/reportes.py --escenario masivo --zona LIM-MIR-01 --usuarios 15 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import urllib.error
import urllib.request
import uuid
from datetime import timedelta

from comun import (
    REGION_POR_DEFECTO,
    ErrorSimulador,
    ahora_utc,
    cargar_zonas,
    crear_cliente,
    epoch_ms,
    exigir_zona,
    iso_z,
    nombre_recurso,
    ruta_datos,
    stage_por_defecto,
)

ESCENARIOS = ("aislado", "masivo", "tormenta")
DESTINOS = ("sqs", "api", "stdout")
TIPOS = ("SIN_INTERNET", "SIN_LLAMADAS", "SIN_SMS", "LENTITUD", "INTERMITENCIA", "OTRO")
LOTE_SQS = 10  # SendMessageBatch admite 10 mensajes; también se usa como lote para la API

NOMBRES = [
    "lucia", "carlos", "maria", "jose", "ana", "luis", "rosa", "miguel", "carmen", "jorge",
    "elena", "pedro", "sofia", "diego", "valeria", "andres", "patricia", "raul", "gabriela", "hugo",
]
APELLIDOS = [
    "quispe", "mamani", "flores", "huaman", "rojas", "vargas", "torres", "castillo",
    "ramos", "chavez", "gutierrez", "sanchez", "rivera", "condori", "lopez", "ccahuana",
]

# Descripciones realistas en español (Perú), agrupadas por tipo de problema.
DESCRIPCIONES = {
    "SIN_INTERNET": [
        "No tengo navegación desde hace una hora, el 4G aparece pero no carga ninguna página.",
        "Sin Internet en el celular y en el router de casa desde esta mañana.",
        "La conexión de datos se queda en 'buscando' y no abre WhatsApp ni redes.",
        "Tengo señal pero no hay datos móviles, ya reinicié el equipo y sigue igual.",
    ],
    "SIN_LLAMADAS": [
        "No puedo hacer ni recibir llamadas, va directo al buzón.",
        "Las llamadas se cortan a los pocos segundos en todo el barrio.",
        "Al marcar sale el tono pero la llamada nunca conecta.",
    ],
    "SIN_SMS": [
        "No me llegan los SMS del banco ni los códigos de verificación.",
        "Mis mensajes de texto se quedan en 'enviando' y no salen.",
        "Desde ayer no recibo ni envío SMS, las llamadas sí funcionan.",
    ],
    "LENTITUD": [
        "La navegación es muy lenta, una página tarda más de un minuto en cargar.",
        "Videos y descargas se traban constantemente, la velocidad es mínima.",
        "Internet anda lento desde la tarde, antes funcionaba bien.",
    ],
    "INTERMITENCIA": [
        "El servicio se cae y vuelve cada pocos minutos durante todo el día.",
        "Tengo señal que va y viene, las llamadas se cortan de forma intermitente.",
        "Se pierde la conexión cada rato y después vuelve sola.",
    ],
    "OTRO": [
        "Problema con el servicio en mi zona, no encaja en las opciones del formulario.",
        "Mi línea muestra un mensaje extraño de error al conectarme a la red.",
    ],
}


def generar_usuario(rng: random.Random) -> dict:
    """Usuario sintético con datos peruanos: DNI de 8 dígitos, celular +519 y correo @example.com."""
    nombre = rng.choice(NOMBRES)
    apellido = rng.choice(APELLIDOS)
    documento = str(rng.randint(1, 9)) + "".join(rng.choice("0123456789") for _ in range(7))
    return {
        "usuarioId": str(uuid.uuid4()),
        "documento": documento,
        "numeroServicio": str(rng.randint(10**8, 10**9 - 1)),
        "telefono": f"+519{rng.randint(10**7, 10**8 - 1)}",
        "email": f"{nombre}.{apellido}{rng.randint(10, 99)}@example.com",
    }


def construir_reporte(zona: dict, tipo: str, usuario: dict, instante, rng: random.Random) -> dict:
    """Cuerpo NuevoReporte (openapi.yaml), con las claves en el orden del contrato."""
    return {
        "documento": usuario["documento"],
        "numeroServicio": usuario["numeroServicio"],
        "telefono": usuario["telefono"],
        "email": usuario["email"],
        "zonaId": zona["zonaId"],
        "lat": round(zona["lat"] + rng.uniform(-0.008, 0.008), 6),
        "lon": round(zona["lon"] + rng.uniform(-0.008, 0.008), 6),
        "fechaHoraEvento": iso_z(instante),
        "tipo": tipo,
        "descripcion": rng.choice(DESCRIPCIONES[tipo]),
    }


def _item(zona: dict, tipo: str, usuario: dict, instante, rng: random.Random) -> dict:
    return {
        "usuarioId": usuario["usuarioId"],
        "incidenciaId": str(uuid.uuid4()),
        "reporte": construir_reporte(zona, tipo, usuario, instante, rng),
    }


def plan_aislado(zonas: list[dict], cantidad: int, rng: random.Random, ahora) -> list[dict]:
    """Reportes dispersos: zona, tipo y hora al azar dentro de las últimas dos horas."""
    items = []
    for _ in range(cantidad):
        zona = rng.choice(zonas)
        tipo = rng.choice(TIPOS)
        instante = ahora - timedelta(seconds=rng.uniform(0, 2 * 3600))
        items.append(_item(zona, tipo, generar_usuario(rng), instante, rng))
    return items


def plan_masivo(zona: dict, usuarios: int, minutos: int, tipo: str, rng: random.Random, ahora) -> list[dict]:
    """Un reporte por usuario distinto en la misma zona, repartidos en la ventana de `minutos`."""
    items = []
    for _ in range(usuarios):
        instante = ahora - timedelta(seconds=rng.uniform(0, minutos * 60))
        items.append(_item(zona, tipo, generar_usuario(rng), instante, rng))
    return sorted(items, key=lambda it: it["reporte"]["fechaHoraEvento"])


def plan_tormenta(zonas: list[dict], cantidad: int, rng: random.Random, ahora) -> list[dict]:
    """Ráfaga: cada reporte en una zona al azar, con usuario nuevo, en los últimos dos minutos."""
    items = []
    for _ in range(cantidad):
        zona = rng.choice(zonas)
        tipo = rng.choice(TIPOS)
        instante = ahora - timedelta(seconds=rng.uniform(0, 120))
        items.append(_item(zona, tipo, generar_usuario(rng), instante, rng))
    return items


def cuerpo_sqs(item: dict, recibido_en_ms: int | None = None) -> dict:
    """Cuerpo del mensaje SQS según integraciones.md sección 1."""
    return {
        "incidenciaId": item["incidenciaId"],
        "usuarioId": item["usuarioId"],
        "recibidoEn": recibido_en_ms if recibido_en_ms is not None else epoch_ms(ahora_utc()),
        "reporte": item["reporte"],
    }


def _lotes(items: list, tamano: int) -> list[list]:
    return [items[i : i + tamano] for i in range(0, len(items), tamano)]


def enviar_sqs(items: list[dict], cola_url: str, cliente) -> int:
    """Envía los mensajes con SendMessageBatch, de 10 en 10."""
    enviados = 0
    for lote in _lotes(items, LOTE_SQS):
        ahora_ms = epoch_ms(ahora_utc())
        entradas = [
            {"Id": str(i), "MessageBody": json.dumps(cuerpo_sqs(it, ahora_ms), ensure_ascii=False)}
            for i, it in enumerate(lote)
        ]
        respuesta = cliente.send_message_batch(QueueUrl=cola_url, Entries=entradas)
        fallidos = respuesta.get("Failed", [])
        if fallidos:
            raise ErrorSimulador(f"SQS rechazó {len(fallidos)} mensajes: {fallidos[0].get('Message')}")
        enviados += len(respuesta.get("Successful", []))
    return enviados


def enviar_api(items: list[dict], api_url: str, token: str) -> int:
    """POST de cada NuevoReporte a la API. Se espera 202 Accepted."""
    enviados = 0
    for it in items:
        datos = json.dumps(it["reporte"], ensure_ascii=False).encode("utf-8")
        peticion = urllib.request.Request(
            api_url,
            data=datos,
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(peticion, timeout=10) as respuesta:
                if respuesta.status != 202:
                    raise ErrorSimulador(f"la API respondió {respuesta.status}, se esperaba 202")
        except urllib.error.HTTPError as e:
            raise ErrorSimulador(f"la API respondió {e.code}: {e.reason}") from e
        enviados += 1
    return enviados


def enviar_reportes(
    items: list[dict],
    destino: str,
    *,
    stage: str,
    region: str,
    api_url: str | None = None,
    token: str | None = None,
    dry_run: bool = False,
    cliente=None,
) -> int:
    """Envía el plan al destino elegido. En dry-run no crea clientes ni hace peticiones."""
    if destino == "stdout":
        for it in items:
            print(json.dumps(cuerpo_sqs(it), ensure_ascii=False))
        return len(items)

    lotes = len(_lotes(items, LOTE_SQS))

    if destino == "sqs":
        nombre_cola = nombre_recurso(stage, "reportes")
        if dry_run:
            print(f"[dry-run] SendMessageBatch a {nombre_cola}: {len(items)} mensajes en {lotes} lotes", file=sys.stderr)
            if items:
                print(f"[dry-run] primer mensaje: {json.dumps(cuerpo_sqs(items[0]), ensure_ascii=False)}", file=sys.stderr)
            return len(items)
        cliente = cliente or crear_cliente("sqs", region)
        cola_url = cliente.get_queue_url(QueueName=nombre_cola)["QueueUrl"]
        return enviar_sqs(items, cola_url, cliente)

    if destino == "api":
        if not api_url or not token:
            raise ErrorSimulador("el destino api requiere --api-url y --token")
        if dry_run:
            print(f"[dry-run] POST {len(items)} reportes a {api_url} en {lotes} lotes", file=sys.stderr)
            if items:
                print(f"[dry-run] primer cuerpo: {json.dumps(items[0]['reporte'], ensure_ascii=False)}", file=sys.stderr)
            return len(items)
        return enviar_api(items, api_url, token)

    raise ErrorSimulador(f"destino desconocido: {destino}")


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simulador de reportes de usuarios de Red TeleCorp.")
    parser.add_argument("--escenario", choices=ESCENARIOS, default="aislado", help="Escenario a simular.")
    parser.add_argument("--zona", help="zonaId de la zona afectada (obligatorio en masivo).")
    parser.add_argument("--usuarios", type=int, default=15, help="Usuarios distintos (masivo).")
    parser.add_argument("--minutos", type=int, default=10, help="Ventana de tiempo de los eventos (masivo).")
    parser.add_argument("--tipo", choices=TIPOS, default="SIN_INTERNET", help="Tipo de problema (masivo).")
    parser.add_argument("--reportes", type=int, help="Cantidad de reportes (aislado: 5; tormenta: 2000).")
    parser.add_argument("--destino", choices=DESTINOS, default="stdout", help="Destino de los reportes.")
    parser.add_argument("--api-url", help="URL del POST /incidencias (destino api).")
    parser.add_argument("--token", help="Token Bearer de Cognito (destino api).")
    parser.add_argument("--stage", default=stage_por_defecto(), help="Stage de despliegue (por defecto STAGE o dev).")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", REGION_POR_DEFECTO), help="Región AWS.")
    parser.add_argument("--datos", help="Carpeta con zonas.json (por defecto datos/ o datos_ejemplo/).")
    parser.add_argument("--semilla", type=int, help="Semilla del generador aleatorio.")
    parser.add_argument("--dry-run", action="store_true", help="Muestra lo que enviaría sin llamar a la nube.")
    return parser


def _construir_plan(args: argparse.Namespace, zonas: list[dict], rng: random.Random) -> list[dict]:
    ahora = ahora_utc()
    if args.escenario == "aislado":
        return plan_aislado(zonas, args.reportes or 5, rng, ahora)
    if args.escenario == "masivo":
        if not args.zona:
            raise ErrorSimulador("el escenario masivo requiere --zona")
        if args.usuarios < 1 or args.minutos < 1:
            raise ErrorSimulador("--usuarios y --minutos deben ser mayores que cero")
        zona = exigir_zona(zonas, args.zona)
        return plan_masivo(zona, args.usuarios, args.minutos, args.tipo, rng, ahora)
    if args.escenario == "tormenta":
        cantidad = args.reportes or 2000
        if cantidad < 1:
            raise ErrorSimulador("--reportes debe ser mayor que cero")
        return plan_tormenta(zonas, cantidad, rng, ahora)
    raise ErrorSimulador(f"escenario desconocido: {args.escenario}")


def _ejecutar(args: argparse.Namespace) -> int:
    datos = ruta_datos(args.datos)
    zonas = cargar_zonas(datos)
    rng = random.Random(args.semilla)

    items = _construir_plan(args, zonas, rng)
    if args.escenario == "masivo":
        distintos = len({it["usuarioId"] for it in items})
        print(f"Masivo: {distintos} usuarios distintos en {args.zona} (umbral colectivo: 10)", file=sys.stderr)

    modo = " (dry-run)" if args.dry_run else ""
    print(f"Escenario {args.escenario} · {len(items)} reportes · destino {args.destino}{modo}", file=sys.stderr)
    enviados = enviar_reportes(
        items,
        args.destino,
        stage=args.stage,
        region=args.region,
        api_url=args.api_url,
        token=args.token,
        dry_run=args.dry_run,
    )
    print(f"Listo: {enviados} reportes {'simulados' if args.dry_run else 'enviados'}", file=sys.stderr)
    return 0


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


if __name__ == "__main__":
    sys.exit(main())
