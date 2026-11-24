from __future__ import annotations

import json
import math
import re
from urllib.parse import urlsplit
from mcp.types import ToolAnnotations

from client import GrafanaError
from tools import get_client
from tools.logs import QUERY_TAGS, search_logs

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
NS_PER_MS = 1_000_000


def _series_from_frames(response: dict) -> list[dict]:
    series = []
    for frame in (response or {}).get("results", {}).get("A", {}).get("frames", []) or []:
        fields = frame.get("schema", {}).get("fields", []) or []
        columns = frame.get("data", {}).get("values", []) or []
        labels: dict = {}
        time_column = None
        value_column = None
        for index, field in enumerate(fields):
            name = field.get("name", "")
            if name in ("Time", "time", "timestamp"):
                time_column = index
            elif value_column is None:
                value_column = index
                labels = field.get("labels", {}) or {}
        if time_column is None or value_column is None:
            continue
        times = columns[time_column]
        values = columns[value_column]
        points = [[times[index], values[index]] for index in range(min(len(times), len(values)))]
        series.append({"labels": labels, "points": points})
    return series


def query_prometheus(
    expr: str,
    from_time: str = "now-1h",
    to_time: str = "now",
    step: str = "1m",
    instant: bool = False,
    datasource_uid: str | None = None,
) -> dict:
    uid = str(get_client().resolve_datasource(datasource_uid, "prometheus")["uid"])
    from_ms, to_ms = get_client().parse_range(from_time, to_time)
    payload = {
        "from": str(from_ms),
        "to": str(to_ms),
        "queries": [
            {
                "refId": "A",
                "datasource": {"type": "prometheus", "uid": uid},
                "expr": expr,
                "instant": instant,
                "range": not instant,
                "step": step,
            }
        ],
    }
    response = get_client().request("POST", "/api/ds/query", json_body=payload)
    return {"series": _series_from_frames(response if isinstance(response, dict) else {})}


def query_loki_metric(
    expr: str,
    from_time: str = "now-1h",
    to_time: str = "now",
    step: str = "1m",
    datasource_uid: str | None = None,
) -> dict:
    uid = str(get_client().resolve_datasource(datasource_uid, "loki")["uid"])
    from_ms, to_ms = get_client().parse_range(from_time, to_time)
    response = get_client().request(
        "GET",
        f"/api/datasources/proxy/uid/{uid}/loki/api/v1/query_range",
        params={
            "query": expr,
            "start": str(from_ms * NS_PER_MS),
            "end": str(to_ms * NS_PER_MS),
            "step": step,
        },
        headers=QUERY_TAGS,
    ) or {}
    result = (response.get("data", {}) or {}).get("result", []) or []
    return {
        "series": [
            {
                "labels": item.get("metric", {}),
                "points": [[point[0], point[1]] for point in item.get("values", []) or []],
            }
            for item in result
        ]
    }


def _percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(ratio * len(ordered)))
    return ordered[rank - 1]


def _duration_ms(line: str) -> float | None:
    match = re.search(r"(?:duration_ms|latency_ms|elapsed_ms|duration)[=:]\s*([0-9]+(?:\.[0-9]+)?)", line)
    if match:
        return float(match.group(1))
    match = re.search(r"(?:^|[\s:;])([0-9]+(?:\.[0-9]+)?)\s*ms\b", line)
    if match:
        return float(match.group(1))
    try:
        payload = json.loads(line)
    except (ValueError, TypeError):
        return None
    for key in ("duration_ms", "latency_ms", "elapsed_ms", "duration"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _trace_id(entry: dict) -> str | None:
    metadata = entry.get("structured_metadata") or {}
    for key in ("trace_id", "traceID", "traceId"):
        if metadata.get(key):
            return str(metadata[key])
    match = re.search(r"(?:trace_id|traceID|traceId)[=:]\s*([0-9a-fA-F]{16,32})", entry.get("line", ""))
    if match:
        return match.group(1)
    match = re.search(r"\[PERF:([0-9a-fA-F]{16,32})\]", entry.get("line", ""))
    return match.group(1) if match else None


def _route_from_line(line: str) -> str | None:
    request = re.search(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(\S+)", line)
    if request:
        path = urlsplit(request.group(2)).path
        if path:
            return f"{request.group(1)} {path}"
    operation = re.search(r"■\s*([A-Za-z0-9_.-]+)", line)
    return operation.group(1) if operation else None

def _auto_source(datasource_uid: str | None, logql: str | None) -> str:
    client = get_client()
    for kind in ("prometheus", "loki"):
        try:
            client.resolve_datasource(datasource_uid, kind)
            return kind
        except GrafanaError:
            continue
    available = sorted({str(ds.get("type", "")) for ds in client.list_datasources()})
    raise GrafanaError(
        0,
        "Neither prometheus nor loki datasource found. "
        f"Available types: {', '.join(available) or 'none'}",
    )


def find_slow_requests(
    source: str = "auto",
    from_time: str = "now-1h",
    to_time: str = "now",
    threshold_ms: float = 500,
    percentile: float = 0.95,
    route_label: str = "route",
    histogram_metric: str = "http_server_request_duration_seconds_bucket",
    logql: str | None = None,
    limit: int = 20,
    datasource_uid: str | None = None,
) -> dict:
    if source not in {"auto", "prometheus", "loki"}:
        raise ValueError("source must be 'auto', 'prometheus', or 'loki'")
    ratio = max(0.0, min(percentile, 1.0))
    capped = max(1, min(limit, 500))
    used_source = _auto_source(datasource_uid, logql) if source == "auto" else source
    if used_source == "loki" and not logql:
        raise ValueError("logql is required when source='loki'")

    if used_source == "prometheus":
        expr = (
            f"histogram_quantile({ratio}, sum by ({route_label}, le) "
            f"(rate({histogram_metric}[5m]))) * 1000"
        )
        series = query_prometheus(
            expr, from_time=from_time, to_time=to_time, datasource_uid=datasource_uid
        )["series"]
        items = []
        for item in series:
            values = [float(point[1]) for point in item["points"] if point[1] is not None]
            if not values:
                continue
            value = values[-1]
            if value < threshold_ms:
                continue
            items.append(
                {
                    "route": item["labels"].get(route_label) or item["labels"].get("route") or "unknown",
                    "p95_ms": round(value, 2),
                    "samples": len(values),
                    "example_trace_id": None,
                }
            )
        truncated = False
    else:
        result = search_logs(
            logql,
            from_time=from_time,
            to_time=to_time,
            limit=500,
            datasource_uid=datasource_uid,
            allow_regex=True,
        )
        grouped: dict[str, list[tuple[float, dict]]] = {}
        for entry in result["entries"]:
            duration = _duration_ms(entry["line"])
            if duration is None:
                continue
            route = str(_route_from_line(entry["line"]) or entry["labels"].get(route_label) or entry["labels"].get("route") or "unknown")
            grouped.setdefault(route, []).append((duration, entry))
        items = []
        for route, pairs in grouped.items():
            value = _percentile([pair[0] for pair in pairs], ratio)
            if value < threshold_ms:
                continue
            trace_ids = [trace for _, entry in pairs if (trace := _trace_id(entry))]
            items.append(
                {
                    "route": route,
                    "p95_ms": round(value, 2),
                    "samples": len(pairs),
                    "example_trace_id": trace_ids[0] if trace_ids else None,
                }
            )
        truncated = result["truncated"]

    items.sort(key=lambda item: item["p95_ms"], reverse=True)
    return {
        "source": used_source,
        "items": items[:capped],
        "threshold_ms": threshold_ms,
        "truncated": truncated or len(items) > capped,
    }


def register(mcp) -> None:
    mcp.tool(name="query_prometheus", annotations=READ_ONLY)(query_prometheus)
    mcp.tool(name="query_loki_metric", annotations=READ_ONLY)(query_loki_metric)
    mcp.tool(name="find_slow_requests", annotations=READ_ONLY)(find_slow_requests)
