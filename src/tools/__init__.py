from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from client import GrafanaClient

_client: "GrafanaClient | None" = None


def get_client() -> "GrafanaClient":
    global _client
    if _client is None:
        from credential_manager import build_client

        _client = build_client()
    return _client


def set_client(client: "GrafanaClient | None") -> None:
    global _client
    _client = client
