from __future__ import annotations

import re
import uuid

from mcp.types import ToolAnnotations

from client import GrafanaError
from tools import get_client

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(destructiveHint=True, openWorldHint=True)


def get_dashboard(uid: str, version: int | None = None) -> dict:
    params = {"version": version} if version is not None else None
    return get_client().request("GET", f"/api/dashboards/uid/{uid}", params=params)


def get_dashboard_summary(uid: str) -> dict:
    dashboard = get_dashboard(uid).get("dashboard", {})
    panels = dashboard.get("panels", [])
    return {
        "uid": dashboard.get("uid", uid),
        "title": dashboard.get("title"),
        "panel_count": len(panels),
        "panel_types": sorted({panel.get("type", "unknown") for panel in panels}),
        "variables": [variable.get("name") for variable in dashboard.get("templating", {}).get("list", [])],
        "time": dashboard.get("time", {}),
    }


def get_dashboard_panel_queries(uid: str) -> list[dict]:
    dashboard = get_dashboard(uid).get("dashboard", {})
    queries = []
    for panel in dashboard.get("panels", []):
        datasource = panel.get("datasource") or {}
        if not isinstance(datasource, dict):
            datasource = {"uid": datasource}
        for target in panel.get("targets", []):
            queries.append(
                {
                    "panel_title": panel.get("title"),
                    "query": target.get("expr") or target.get("query") or "",
                    "datasource": {
                        "type": datasource.get("type"),
                        "uid": datasource.get("uid"),
                    },
                }
            )
    return queries


def list_dashboard_versions(uid: str) -> list[dict]:
    result = get_client().request("GET", f"/api/dashboards/uid/{uid}/versions")
    return result if isinstance(result, list) else []


def _slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "dashboard"
    return f"{base}-{uuid.uuid4().hex[:6]}"


def create_dashboard(
    title: str,
    panels: list[dict] | None = None,
    uid: str | None = None,
    folder_uid: str | None = None,
    overwrite: bool = False,
    confirm: bool = False,
    datasource_uid: str | None = None,
) -> dict:
    if not confirm:
        raise ValueError("create_dashboard requires confirm=True")
    client = get_client()
    dashboard_uid = uid or _slug(title)
    note = None

    if panels is None:
        datasource = None
        for hint in ("prometheus", "grafana"):
            try:
                datasource = client.resolve_datasource(datasource_uid, hint)
                break
            except GrafanaError:
                if datasource_uid:
                    raise
        if datasource:
            panels = [
                {
                    "type": "timeseries",
                    "title": title,
                    "datasource": {"type": datasource.get("type"), "uid": datasource.get("uid")},
                    "targets": [
                        {
                            "refId": "A",
                            "query": "up" if datasource.get("type") == "prometheus" else "",
                            "expr": "up" if datasource.get("type") == "prometheus" else "",
                        }
                    ],
                }
            ]
        else:
            panels = []
            note = "No datasource available; created dashboard with an empty panel list."

    model = {
        "uid": dashboard_uid,
        "title": title,
        "panels": panels,
        "schemaVersion": 41,
        "version": 0,
        "editable": True,
        "time": {"from": "now-6h", "to": "now"},
    }
    payload = {"dashboard": model, "folderUid": folder_uid, "overwrite": overwrite}
    try:
        result = client.request("POST", "/api/dashboards/db", json_body=payload)
    except GrafanaError as exc:
        if exc.status != 404:
            raise
        fallback = {
            "metadata": {
                "name": dashboard_uid,
                "annotations": {"grafana.app/folder": folder_uid},
            },
            "spec": model,
        }
        result = client.request(
            "POST",
            "/apis/dashboard.grafana.app/v1/namespaces/default/dashboards",
            json_body=fallback,
        )
    if note:
        return {"result": result, "note": note}
    return result if isinstance(result, dict) else {"result": result}


def delete_dashboard(uid: str, confirm: bool = False) -> dict:
    if not confirm:
        raise ValueError("delete_dashboard requires confirm=True")
    return get_client().request("DELETE", f"/api/dashboards/uid/{uid}")


def create_folder(title: str, uid: str | None = None, confirm: bool = False) -> dict:
    if not confirm:
        raise ValueError("create_folder requires confirm=True")
    payload: dict = {"title": title}
    if uid:
        payload["uid"] = uid
    return get_client().request("POST", "/api/folders", json_body=payload)


def register(mcp) -> None:
    mcp.tool(name="get_dashboard", annotations=READ_ONLY)(get_dashboard)
    mcp.tool(name="get_dashboard_summary", annotations=READ_ONLY)(get_dashboard_summary)
    mcp.tool(name="get_dashboard_panel_queries", annotations=READ_ONLY)(get_dashboard_panel_queries)
    mcp.tool(name="list_dashboard_versions", annotations=READ_ONLY)(list_dashboard_versions)
    mcp.tool(name="create_dashboard", annotations=WRITE)(create_dashboard)
    mcp.tool(name="delete_dashboard", annotations=DESTRUCTIVE)(delete_dashboard)
    mcp.tool(name="create_folder", annotations=WRITE)(create_folder)
