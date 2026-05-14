"""Webhook NotificationSink — POST a JSON event to a configured URL.

Generic shape: works with Slack-incoming-webhooks (when the operator
provides a Slack URL and a custom message template), or with any HTTP
receiver expecting `{level, message, context}` JSON.

Fire-and-forget at the Protocol level: errors are logged and swallowed.
The pipeline does not retry. Customers who want reliable delivery wrap
this in a queue.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from urllib.error import URLError

from shared.protocols.notification_sink import EventLevel

logger = logging.getLogger(__name__)


class WebhookNotificationSink:
    """POST JSON to `url`. Returns None on success or any failure."""

    name = "webhook"

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 5.0,
        allow_http: bool = False,
    ) -> None:
        """`url` must be https:// unless `allow_http` is True.

        The https-only default is the cheapest defense against
        accidental SSRF / interception when the webhook URL is moved
        from env-config into operator-facing settings later. Customers
        who *intentionally* want to hit an internal http:// receiver
        pass `allow_http=True` at construction time.
        """
        if not allow_http and not url.lower().startswith("https://"):
            raise ValueError(
                f"webhook URL must use https:// (got {url!r}); "
                f"pass allow_http=True to override"
            )
        self._url = url
        self._timeout = timeout_seconds

    def notify(
        self,
        level: EventLevel,
        message: str,
        *,
        context: dict[str, str] | None = None,
    ) -> None:
        payload = {
            "level": level.value,
            "message": message,
            "context": context or {},
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 — operator-supplied URL
                resp.read()  # drain, but we don't care about the body
        except (URLError, TimeoutError, OSError) as exc:
            logger.warning("webhook notify failed: %s", exc)
