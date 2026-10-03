"""SMTP notification. Recipients come only from the connection's configured list."""

from __future__ import annotations

import asyncio
import json
import smtplib
from email.message import EmailMessage

from idp.config import Settings
from idp.infrastructure.db.models import Connection
from idp.providers.actions.base import ActionError, ActionOutcome, ActionRequest
from idp.providers.enrichment.base import EmailConfig


class EmailActionProvider:
    kind = "email"
    is_mock = False

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def configured(self) -> bool:
        return bool(self._settings.smtp_host and self._settings.smtp_from)

    def _send(self, message: EmailMessage) -> None:
        s = self._settings
        assert s.smtp_host  # noqa: S101 — guarded by configured()
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            password = s.smtp_password.get_secret_value()
            if s.smtp_username and password:
                smtp.login(s.smtp_username, password)
            smtp.send_message(message)

    async def execute(self, connection: Connection, request: ActionRequest) -> ActionOutcome:
        if not self.configured():
            raise ActionError("smtp_not_configured")
        config = EmailConfig.model_validate(connection.config)
        message = EmailMessage()
        message["Subject"] = (
            f"{config.subject_prefix} {request.action}: document {request.document_id}"
        )
        message["From"] = self._settings.smtp_from or ""
        message["To"] = ", ".join(config.to)
        message["Message-ID"] = f"<{request.idempotency_key.replace(':', '.')}@idp>"
        message.set_content(json.dumps(request.payload, indent=2, default=str, ensure_ascii=False))
        try:
            await asyncio.to_thread(self._send, message)
        except (
            smtplib.SMTPServerDisconnected,
            smtplib.SMTPConnectError,
            TimeoutError,
            OSError,
        ) as exc:
            raise ActionError("smtp_unavailable", transient=True) from exc
        except smtplib.SMTPException as exc:
            raise ActionError("smtp_rejected") from exc
        return ActionOutcome(response={"recipients": len(config.to)})
