from __future__ import annotations

from mcp.types import ToolAnnotations

from tools import get_client

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(openWorldHint=True)

FIELDS = ("id", "uid", "name", "type", "url", "access", "isDefault")


def list_datasources(type_hint: str | None = None) -> list[dict]:
    result = get_client().request("GET", "/api/datasources")
    if not isinstance(result, list):
        return []
    items = [{key: item.get(key) for key in FIELDS} for item in result]
    if type_hint:
        items = [item for item in items if str(item.get("type", "")).lower() == type_hint.lower()]
    return items


def get_datasource(uid: str) -> dict:
    return get_client().request("GET", f"/api/datasources/uid/{uid}")


def datasource_health(uid: str) -> dict:
    return get_client().request("GET", f"/api/datasources/uid/{uid}/health")


def create_datasource(
    name: str,
    type: str,
    url: str,
    access: str = "proxy",
    is_default: bool = False,
    json_data: dict | None = None,
    confirm: bool = False,
) -> dict:
    if not confirm:
        raise ValueError("create_datasource requires confirm=True")
    payload: dict = {
        "name": name,
        "type": type,
        "url": url,
        "access": access,
        "isDefault": is_default,
    }
    if json_data:
        payload["jsonData"] = json_data
    return get_client().request("POST", "/api/datasources", json_body=payload)


def update_datasource(uid: str, changes: dict, confirm: bool = False) -> dict:
    if not confirm:
        raise ValueError("update_datasource requires confirm=True")
    return get_client().request("PUT", f"/api/datasources/uid/{uid}", json_body=changes)


def register(mcp) -> None:
    mcp.tool(name="list_datasources", annotations=READ_ONLY)(list_datasources)
    mcp.tool(name="get_datasource", annotations=READ_ONLY)(get_datasource)
    mcp.tool(name="datasource_health", annotations=READ_ONLY)(datasource_health)
    mcp.tool(name="create_datasource", annotations=WRITE)(create_datasource)
    mcp.tool(name="update_datasource", annotations=WRITE)(update_datasource)
