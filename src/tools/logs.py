from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from mcp.types import ToolAnnotations

from client import GrafanaClient
from tools import get_client

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
NS_PER_MS = 1_000_000
WINDOW_MS = 15 * 60 * 1000
MAX_RANGE_MS = 24 * 60 * 60 * 1000
QUERY_TAGS = {"X-Query-Tags": "Source=grafana-custom-mcp"}


def _proxy_path(uid: str, endpoint: str) -> str:
    return f"/api/datasources/proxy/uid/{uid}/loki/api/v1/{endpoint}"


def _resolve_uid(datasource_uid: str | None) -> str:
    datasource = get_client().resolve_datasource(datasource_uid, "loki")
    return str(datasource["uid"])


def _parse_ns(value: str) -> int:
    text = str(value).strip()
    if re.fullmatch(r"\d+", text):
        number = int(text)
        if len(text) >= 19:
            return number
        if len(text) >= 16:
            return number * 1_000
        if len(text) >= 13:
            return number * NS_PER_MS
        return number * 1_000_000_000
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


def _duration_ms(value: str) -> int:
    match = re.fullmatch(r"(\d+)([smhdw])", str(value).strip())
    if not match:
        raise ValueError(f"Unsupported duration: {value}")
    multipliers = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    return int(match.group(1)) * multipliers[match.group(2)]


def loki_label_names(
    datasource_uid: str | None = None,
    from_time: str = "now-1h",
    to_time: str = "now",
    matcher: str = "",
) -> list[str]:
    uid = _resolve_uid(datasource_uid)
    start_ms, end_ms = GrafanaClient.parse_range(from_time, to_time)
    params: dict = {"start": str(start_ms * NS_PER_MS), "end": str(end_ms * NS_PER_MS)}
    if matcher:
        params["query"] = matcher
    result = get_client().request(
        "GET", _proxy_path(uid, "labels"), params=params, headers=QUERY_TAGS
    )
    return list((result or {}).get("data", []))


def loki_label_values(
    label: str,
    datasource_uid: str | None = None,
    from_time: str = "now-1h",
    to_time: str = "now",
    matcher: str = "",
) -> list[str]:
    uid = _resolve_uid(datasource_uid)
    start_ms, end_ms = GrafanaClient.parse_range(from_time, to_time)
    params: dict = {"start": str(start_ms * NS_PER_MS), "end": str(end_ms * NS_PER_MS)}
    if matcher:
        params["query"] = matcher
    result = get_client().request(
        "GET", _proxy_path(uid, f"label/{label}/values"), params=params, headers=QUERY_TAGS
    )
    return list((result or {}).get("data", []))


def search_logs(
    logql: str,
    from_time: str = "now-15m",
    to_time: str = "now",
    limit: int = 50,
    datasource_uid: str | None = None,
    before: str | None = None,
    allow_regex: bool = False,
) -> dict:
    if "{" not in logql or "}" not in logql:
        raise ValueError("logql must contain a stream selector like {job=\"api\"}")
    if not allow_regex and ("|=~" in logql or "!~" in logql):
        raise ValueError("Regex filters are disabled; set allow_regex=True to enable them")

    capped = max(1, min(limit, 500))
    uid = _resolve_uid(datasource_uid)
    start_ms, end_ms = GrafanaClient.parse_range(from_time, to_time)
    if before is not None:
        end_ms = _parse_ns(before) // NS_PER_MS
        if start_ms >= end_ms:
            raise ValueError("before must be later than from_time")
    if end_ms - start_ms > MAX_RANGE_MS:
        raise ValueError("log search range must not exceed 24 hours per call")

    entries: list[dict] = []
    accumulated_stats: dict = {}
    windows_scanned = 0
    truncated = False
    cursor_end = end_ms

    while cursor_end > start_ms and len(entries) < capped:
        cursor_start = max(start_ms, cursor_end - WINDOW_MS)
        params = {
            "query": logql,
            "start": str(cursor_start * NS_PER_MS),
            "end": str(cursor_end * NS_PER_MS),
            "limit": capped + 1,
            "direction": "backward",
        }
        response = get_client().request(
            "GET", _proxy_path(uid, "query_range"), params=params, headers=QUERY_TAGS
        ) or {}
        data = response.get("data", {}) or {}
        summary = (response.get("stats") or data.get("stats") or {}).get("summary", {})
        for key, value in summary.items():
            if isinstance(value, (int, float)):
                accumulated_stats[key] = accumulated_stats.get(key, 0) + value

        for stream in data.get("result", []) or []:
            metric = stream.get("metric", {})
            for item in stream.get("values", []) or []:
                timestamp_ns = str(item[0])
                line = item[1] if len(item) > 1 else ""
                metadata = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
                entries.append(
                    {
                        "timestamp_ns": timestamp_ns,
                        "line": line,
                        "labels": metric,
                        "structured_metadata": metadata,
                    }
                )
        windows_scanned += 1
        if len(entries) > capped:
            truncated = True
            break
        cursor_end = cursor_start

    if cursor_end > start_ms and len(entries) >= capped:
        truncated = True

    entries = entries[:capped]
    entries.sort(key=lambda entry: int(entry["timestamp_ns"]), reverse=True)
    return {
        "entries": entries,
        "truncated": truncated,
        "oldest_timestamp_ns": entries[-1]["timestamp_ns"] if entries else None,
        "stats": accumulated_stats,
        "windows_scanned": windows_scanned,
    }


def log_context(
    timestamp_ns: str,
    labels: dict,
    datasource_uid: str | None = None,
    window: str = "2m",
    limit: int = 20,
) -> dict:
    center_ns = _parse_ns(timestamp_ns)
    half_window = _duration_ms(window)
    selector = "{" + ", ".join(
        f"{key}={json.dumps(str(value))}" for key, value in labels.items()
    ) + "}"
    result = search_logs(
        selector,
        from_time=str(center_ns // NS_PER_MS - half_window),
        to_time=str(center_ns // NS_PER_MS + half_window),
        limit=limit,
        datasource_uid=datasource_uid,
    )
    return {**result, "center_timestamp_ns": str(center_ns), "labels": labels}


def log_count(
    logql: str,
    from_time: str = "now-1h",
    to_time: str = "now",
    step: str = "1m",
    datasource_uid: str | None = None,
) -> list[dict]:
    uid = _resolve_uid(datasource_uid)
    start_ms, end_ms = GrafanaClient.parse_range(from_time, to_time)
    params = {
        "query": logql,
        "start": str(start_ms * NS_PER_MS),
        "end": str(end_ms * NS_PER_MS),
        "step": step,
    }
    response = get_client().request(
        "GET", _proxy_path(uid, "query_range"), params=params, headers=QUERY_TAGS
    ) or {}
    series = (response.get("data", {}) or {}).get("result", []) or []
    return [
        {
            "labels": item.get("metric", {}),
            "points": [[point[0], point[1]] for point in item.get("values", []) or []],
        }
        for item in series
    ]


def register(mcp) -> None:
    mcp.tool(name="loki_label_names", annotations=READ_ONLY)(loki_label_names)
    mcp.tool(name="loki_label_values", annotations=READ_ONLY)(loki_label_values)
    mcp.tool(name="search_logs", annotations=READ_ONLY)(search_logs)
    mcp.tool(name="log_context", annotations=READ_ONLY)(log_context)
    mcp.tool(name="log_count", annotations=READ_ONLY)(log_count)
