from __future__ import annotations

from mcp.types import ToolAnnotations

from tools import get_client

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
PASSTHROUGH = ToolAnnotations(openWorldHint=True)


def grafana_health() -> dict:
    return get_client().request("GET", "/api/health")


def grafana_whoami() -> dict:
    return get_client().request("GET", "/api/user")


def grafana_api(
    method: str,
    path: str,
    params: dict | None = None,
    body: dict | None = None,
    confirm: bool = False,
) -> dict | list | str:
    """Passthrough ke seluruh endpoint /api/** dan /apis/** Grafana."""
    normalized = method.upper()
    if normalized not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError(f"Unsupported HTTP method: {method}")
    if normalized != "GET" and normalized != "HEAD" and not confirm:
        raise ValueError("grafana_api requires confirm=True for non-GET methods")
    return get_client().request(
        normalized,
        path,
        params=params,
        json_body=body,
    )


def search_dashboards(
    query: str = "",
    limit: int = 20,
    tag: str | None = None,
    folder_uid: str | None = None,
) -> list[dict]:
    params: dict = {"type": "dash-db", "limit": max(1, min(limit, 100))}
    if query:
        params["query"] = query
    if tag:
        params["tag"] = tag
    if folder_uid:
        params["folderUID"] = folder_uid
    result = get_client().request("GET", "/api/search", params=params)
    return result if isinstance(result, list) else []


def search_folders(query: str = "", limit: int = 20) -> list[dict]:
    params: dict = {"type": "dash-folder", "limit": max(1, min(limit, 100))}
    if query:
        params["query"] = query
    result = get_client().request("GET", "/api/search", params=params)
    return result if isinstance(result, list) else []


def register(mcp) -> None:
    mcp.tool(name="grafana_health", annotations=READ_ONLY)(grafana_health)
    mcp.tool(name="grafana_whoami", annotations=READ_ONLY)(grafana_whoami)
    mcp.tool(name="grafana_api", annotations=PASSTHROUGH)(grafana_api)
    mcp.tool(name="search_dashboards", annotations=READ_ONLY)(search_dashboards)
    mcp.tool(name="search_folders", annotations=READ_ONLY)(search_folders)
