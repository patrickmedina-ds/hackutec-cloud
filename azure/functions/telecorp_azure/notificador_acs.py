"""Envío de avisos de respaldo por Azure Communication Services (SMS y correo)."""
from __future__ import annotations

import os


class NotificadorACS:
    def __init__(self):
        self._cadena = os.environ.get("ACS_CONNECTION_STRING")
        self._endpoint = os.environ.get("ACS_ENDPOINT")
        self._remitente_sms = os.environ.get("ACS_SMS_REMITENTE", "")
        self._remitente_correo = os.environ.get("ACS_EMAIL_REMITENTE", "")

    def _cliente(self, clase):
        if self._cadena:
            return clase.from_connection_string(self._cadena)
        from azure.identity import DefaultAzureCredential
        return clase(self._endpoint, DefaultAzureCredential())

    def enviar(self, canal: str, destino: str, asunto: str | None, mensaje: str) -> None:
        if canal == "SMS":
            self._enviar_sms(destino, mensaje)
        elif canal == "EMAIL":
            self._enviar_correo(destino, asunto or "", mensaje)
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

    def _enviar_correo(self, destino: str, asunto: str, mensaje: str) -> None:
        from azure.communication.email import EmailClient
        poller = self._cliente(EmailClient).begin_send({
            "senderAddress": self._remitente_correo,
            "recipients": {"to": [{"address": destino}]},
            "content": {"subject": asunto, "plainText": mensaje},
        })
        resultado = poller.result()
        if resultado.get("status") != "Succeeded":
            raise RuntimeError(f"ACS correo falló: {resultado.get('status')}")
