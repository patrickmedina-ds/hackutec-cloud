"""Envío de avisos por Azure Communication Services (canal principal: SMS y correo)."""
from __future__ import annotations

import os
from html import escape

ESTADOS_LEGIBLES = {
    "REGISTRADA": "En evaluación",
    "EN_REVISION": "En evaluación",
    "ATENDIDA": "Incidencia identificada",
    "SOLUCIONADA": "Servicio restablecido",
    "CERRADA": "Servicio restablecido",
}


def estado_legible(estado: str | None) -> str:
    return ESTADOS_LEGIBLES.get(estado or "", estado or "")


def construir_texto(mensaje: str, incidencia_id: str | None = None,
                    estado: str | None = None) -> str:
    """Cuerpo en texto plano. Gmail no entrega correos HTML desde el dominio
    administrado de Azure (azurecomm.net); el texto plano sí llega."""
    lineas = [mensaje, ""]
    if estado:
        lineas.append(f"Estado: {estado_legible(estado)}")
    if incidencia_id:
        lineas.append(f"Incidencia: {incidencia_id}")
    lineas += ["", "Aviso automático de Red TeleCorp."]
    return "\n".join(lineas)


def construir_html(asunto: str, mensaje: str, incidencia_id: str | None = None,
                   estado: str | None = None) -> str:
    """Correo HTML sencillo (estilos en línea, sin recursos externos)."""
    filas = ""
    if estado:
        filas += f"<p style=\"margin:0 0 4px\"><strong>Estado:</strong> {escape(estado_legible(estado))}</p>"
    if incidencia_id:
        filas += f"<p style=\"margin:0 0 4px\"><strong>Incidencia:</strong> {escape(incidencia_id)}</p>"
    cuerpo = escape(mensaje).replace("\n", "<br>")
    return (
        "<!DOCTYPE html><html lang=\"es\"><body style=\"margin:0;background:#f4f6f8;"
        "font-family:Arial,Helvetica,sans-serif;color:#1f2933\">"
        "<div style=\"max-width:560px;margin:0 auto;background:#ffffff\">"
        "<div style=\"background:#0b5cad;color:#ffffff;padding:16px 24px;font-size:18px\">"
        f"Red TeleCorp</div><div style=\"padding:24px\"><h2 style=\"margin:0 0 16px;font-size:20px\">"
        f"{escape(asunto)}</h2><p style=\"margin:0 0 16px;line-height:1.5\">{cuerpo}</p>{filas}"
        "</div><div style=\"padding:12px 24px;font-size:12px;color:#6b7785\">"
        "Aviso automático de Red TeleCorp.</div></div></body></html>"
    )


class NotificadorACS:
    def __init__(self):
        self._cadena = os.environ.get("ACS_CONNECTION_STRING")
        self._endpoint = os.environ.get("ACS_ENDPOINT")
        self._remitente_sms = os.environ.get("ACS_SMS_REMITENTE", "")
        self._remitente_correo = os.environ.get("ACS_EMAIL_REMITENTE", "")

    @property
    def sms_habilitado(self) -> bool:
        return bool(self._remitente_sms)

    def _cliente(self, clase):
        if self._cadena:
            return clase.from_connection_string(self._cadena)
        from azure.identity import DefaultAzureCredential
        return clase(self._endpoint, DefaultAzureCredential())

    def enviar(self, canal: str, destino: str, asunto: str | None, mensaje: str,
               incidencia_id: str | None = None, estado: str | None = None) -> None:
        if canal == "SMS":
            self._enviar_sms(destino, mensaje)
        elif canal == "EMAIL":
            self._enviar_correo(destino, asunto or "", mensaje, incidencia_id, estado)
        else:
            raise ValueError(f"Canal no soportado: {canal}")

    def _enviar_sms(self, destino: str, mensaje: str) -> None:
        if not self._remitente_sms:
            raise RuntimeError("ACS_SMS_REMITENTE no configurado (falta comprar un número)")
        from azure.communication.sms import SmsClient
        resultados = self._cliente(SmsClient).send(
            from_=self._remitente_sms, to=[destino], message=mensaje)
        for r in resultados:
            if not r.successful:
                raise RuntimeError(f"ACS SMS falló: {r.http_status_code} {r.error_message}")

    def _enviar_correo(self, destino: str, asunto: str, mensaje: str,
                       incidencia_id: str | None = None, estado: str | None = None) -> None:
        from azure.communication.email import EmailClient
        poller = self._cliente(EmailClient).begin_send({
            "senderAddress": self._remitente_correo,
            "recipients": {"to": [{"address": destino}]},
            "content": {"subject": asunto,
                        "plainText": construir_texto(mensaje, incidencia_id, estado)},
        })
        resultado = poller.result()
        if resultado.get("status") != "Succeeded":
            raise RuntimeError(f"ACS correo falló: {resultado.get('status')}")
