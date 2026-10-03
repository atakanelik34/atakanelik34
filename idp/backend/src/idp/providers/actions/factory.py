from __future__ import annotations

from idp.config import Settings
from idp.providers.actions.base import ActionProvider
from idp.providers.actions.email import EmailActionProvider
from idp.providers.actions.mock_erp import MockERPActionProvider
from idp.providers.actions.webhook import WebhookActionProvider, WebhookSender


def create_webhook_sender(settings: Settings) -> WebhookSender:
    return WebhookSender(
        allowed_hosts=settings.webhook_allowed_host_set,
        timeout_seconds=settings.webhook_timeout_seconds,
    )


def create_action_providers(settings: Settings, sender: WebhookSender) -> dict[str, ActionProvider]:
    return {
        "webhook": WebhookActionProvider(sender),
        "email": EmailActionProvider(settings),
        "mock_erp": MockERPActionProvider(),
    }
