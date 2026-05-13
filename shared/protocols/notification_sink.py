"""NotificationSink Protocol: where pipeline events get reported.

Concrete implementations live in `plugins/notification_sink/`. POC order:
webhook first (Slack / generic JSON POST), email later.

Notifications are intentionally fire-and-forget at the Protocol level. If a
sink fails, the pipeline does not retry — operator visibility into the
*document* is the durable thing, the notification is a convenience. The
sink may retry internally on transient errors if it wants to.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable


class EventLevel(str, Enum):
    """Severity of a pipeline event, used for routing decisions inside sinks.

    A Slack channel might subscribe only to `ERROR`+; an email digest might
    include everything. Sinks decide; this enum is the shared vocabulary.
    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@runtime_checkable
class NotificationSink(Protocol):
    """A destination for human-readable pipeline events."""

    name: str
    """Stable identifier (e.g. `"slack-webhook"`)."""

    def notify(self, level: EventLevel, message: str, *, context: dict[str, str] | None = None) -> None:
        """Deliver a single event.

        Args:
            level: Severity. Sinks may filter on this.
            message: Operator-readable summary. Keep it one line if possible.
            context: Optional small dict of stable keys (`document_id`,
                `session_id`, `vendor`, etc.). Sinks may render this as a
                table, slack-attachment, or ignore it entirely.
        """
        ...
