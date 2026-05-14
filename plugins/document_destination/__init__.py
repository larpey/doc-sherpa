"""DocumentDestination plugins. POC priority order: filesystem → S3 → Drive → SharePoint."""

from .filesystem import FilesystemDestination

__all__ = ["FilesystemDestination"]
