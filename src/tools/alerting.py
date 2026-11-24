from __future__ import annotations

from mcp.types import ToolAnnotations

from tools import get_client

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def list_alert_rules(
    folder_uid: str | None = None,
    state: str | None = None,
    limit: int = 50,
) -> list[dict]:
    result = get_client().request("GET", "/api/v1/provisioning/alert-rules")
    rules = result if isinstance(result, list) else []
    if folder_uid:
        rules = [rule for rule in rules if rule.get("folderUID") == folder_uid]
    if state:
        rules = [rule for rule in rules if str(rule.get("state", "")).lower() == state.lower()]
    return rules[: max(1, min(limit, 100))]


def get_alert_rule(uid: str) -> dict:
    return get_client().request("GET", f"/api/v1/provisioning/alert-rules/{uid}")


def list_contact_points() -> list[dict]:
    result = get_client().request("GET", "/api/v1/provisioning/contact-points")
    return result if isinstance(result, list) else []


def list_alert_groups() -> list[dict]:
    result = get_client().request("GET", "/api/alertmanager/grafana/api/v2/alerts")
    return result if isinstance(result, list) else []


def register(mcp) -> None:
    mcp.tool(name="list_alert_rules", annotations=READ_ONLY)(list_alert_rules)
    mcp.tool(name="get_alert_rule", annotations=READ_ONLY)(get_alert_rule)
    mcp.tool(name="list_contact_points", annotations=READ_ONLY)(list_contact_points)
    mcp.tool(name="list_alert_groups", annotations=READ_ONLY)(list_alert_groups)
