"""
mailer.py
---------
Autenticacion OAuth2 (Client Credentials) contra Microsoft Entra ID y envio
de correos via Microsoft Graph API.

Requiere que la aplicacion registrada en Entra ID tenga el permiso de
aplicacion (Application permission) "Mail.Send" con consentimiento de
administrador otorgado, y que el buzon remitente (MAIL_SENDER) sea una
cuenta valida del tenant.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

import msal
import requests

logger = logging.getLogger("app-normatividad.mailer")

GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
GRAPH_SEND_MAIL_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"


class GraphMailError(RuntimeError):
    """Error al autenticar o enviar correo a traves de Microsoft Graph."""


class GraphMailer:
    """
    Envoltorio simple sobre MSAL + Graph para enviar correos con el flujo
    de credenciales de cliente (aplicacion a aplicacion, sin usuario logueado).
    """

    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        if not all([tenant_id, client_id, client_secret]):
            raise ValueError(
                "tenant_id, client_id y client_secret son obligatorios para GraphMailer"
            )
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._authority = f"https://login.microsoftonline.com/{tenant_id}"
        self._app = msal.ConfidentialClientApplication(
            client_id=self._client_id,
            client_credential=self._client_secret,
            authority=self._authority,
        )

    def _obtener_token(self) -> str:
        """Obtiene un access token, usando la cache interna de MSAL si es valida."""
        resultado = self._app.acquire_token_silent(GRAPH_SCOPE, account=None)
        if not resultado:
            logger.debug("No hay token en cache; solicitando uno nuevo a Entra ID")
            resultado = self._app.acquire_token_for_client(scopes=GRAPH_SCOPE)

        if "access_token" not in resultado:
            error = resultado.get("error")
            error_desc = resultado.get("error_description")
            raise GraphMailError(
                f"No se pudo obtener token de acceso ({error}): {error_desc}"
            )
        return resultado["access_token"]

    def enviar_correo(
        self,
        sender: str,
        destinatarios: Iterable[str],
        asunto: str,
        cuerpo_html: str,
        cco: Optional[Iterable[str]] = None,
        timeout: int = 30,
    ) -> None:
        """
        Envia un correo HTML usando el endpoint sendMail de Graph, actuando
        como el buzon `sender` (requiere permiso de aplicacion Mail.Send).
        """
        token = self._obtener_token()

        destinatarios = [d.strip() for d in destinatarios if d and d.strip()]
        if not destinatarios:
            raise ValueError("Debe indicarse al menos un destinatario")

        mensaje: dict = {
            "message": {
                "subject": asunto,
                "body": {"contentType": "HTML", "content": cuerpo_html},
                "toRecipients": [
                    {"emailAddress": {"address": addr}} for addr in destinatarios
                ],
            },
            "saveToSentItems": "true",
        }

        if cco:
            cco_list = [d.strip() for d in cco if d and d.strip()]
            if cco_list:
                mensaje["message"]["bccRecipients"] = [
                    {"emailAddress": {"address": addr}} for addr in cco_list
                ]

        url = GRAPH_SEND_MAIL_URL.format(sender=sender)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        logger.info(
            "Enviando correo via Microsoft Graph: remitente=%s destinatarios=%s asunto=%r",
            sender,
            destinatarios,
            asunto,
        )
        resp = requests.post(url, headers=headers, json=mensaje, timeout=timeout)

        if resp.status_code == 202:
            logger.info("Correo enviado correctamente (202 Accepted)")
            return

        logger.error(
            "Fallo al enviar correo. status=%s body=%s", resp.status_code, resp.text
        )
        raise GraphMailError(
            f"Microsoft Graph devolvio {resp.status_code} al enviar el correo: {resp.text}"
        )
