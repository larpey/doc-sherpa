"""Plugin Protocols. One Protocol per file; this module just re-exports them.

These are *interfaces*, not base classes. Concrete plugins in `plugins/` do
not need to inherit from anything — they just have to be structurally
compatible (typing.Protocol with `runtime_checkable` where useful).
"""

from .ai_classifier import AIClassifier
from .document_destination import DocumentDestination
from .document_ingest import DocumentIngest
from .notification_sink import NotificationSink
from .registry import PluginRegistry

__all__ = [
    "AIClassifier",
    "DocumentDestination",
    "DocumentIngest",
    "NotificationSink",
    "PluginRegistry",
]
