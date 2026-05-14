"""NotificationSink plugins. POC ships a generic webhook sink."""

from .webhook import WebhookNotificationSink

__all__ = ["WebhookNotificationSink"]
