from __future__ import annotations

from mcp.types import ToolAnnotations

from client import GrafanaError
from tools import get_client
from tools.logs import search_logs

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
LLM_ACCEPT = {"Accept": "application/vnd.grafana.llm"}
NS_PER_MS = 1_000_000
NS_PER_S = 1_000_000_000


def _resolve_uid(datasource_uid: str | None) -> str:
    datasource = get_client().resolve_datasource(datasource_uid, "tempo")
    return str(datasource["uid"])


def _span_count(nested) -> int | None:
    if not isinstance(nested, list):
        return None
    counts = []
    for frame in nested:
        if not isinstance(frame, dict):
            continue
        values = (frame.get("data", {}) or {}).get("values", []) or []
        names = [field.get("name") for field in (frame.get("schema", {}) or {}).get("fields", []) or []]
        column = values[names.index("spanID")] if "spanID" in names else None
        if column is not None:
            counts.append(len(column))
        elif values:
            counts.append(len(values[0]))
    return sum(counts) if counts else None


def _duration_value(row: dict) -> float:
    for key in ("durationMs", "duration_ms", "traceDuration"):
        if row.get(key) is not None:
            return float(row[key])
    return float(row.get("durationNs", 0)) / NS_PER_MS


def _traces_from_frames(response: dict) -> list[dict]:
    traces = []
    for frame in (response.get("results", {}).get("A", {}).get("frames", []) or []):
        fields = [field.get("name") for field in frame.get("schema", {}).get("fields", []) or []]
        columns = frame.get("data", {}).get("values", []) or []
        for index in range(max((len(column) for column in columns), default=0)):
            row = {name: column[index] for name, column in zip(fields, columns) if index < len(column)}
            traces.append(
                {
                    "trace_id": str(row.get("traceID") or row.get("traceId") or row.get("trace_id") or ""),
                    "root_service": (
                        row.get("rootServiceName") or row.get("root_service")
                        or row.get("traceService") or row.get("serviceName")
                    ),
                    "duration_ms": _duration_value(row),
                    "status": row.get("status") or row.get("rootStatus"),
                    "span_count": row.get("spanCount", row.get("span_count", _span_count(row.get("nested")))),
                }
            )
    return traces


def _traces_from_proxy(response: dict | list) -> list[dict]:
    payload = response if isinstance(response, list) else (response.get("traces") or response.get("data") or [])
    traces = []
    for item in payload:
        if isinstance(item, dict):
            traces.append(
                {
                    "trace_id": str(item.get("traceID") or item.get("traceId") or item.get("trace_id") or ""),
                    "root_service": item.get("rootServiceName") or item.get("root_service"),
                    "duration_ms": item.get("durationMs", item.get("duration_ms", item.get("durationNs", 0) / NS_PER_MS)),
                    "status": item.get("status") or item.get("rootStatus"),
                    "span_count": item.get("spanCount", item.get("span_count")),
                }
            )
    return traces


def search_traces(
    traceql: str = "{}",
    from_time: str = "now-1h",
    to_time: str = "now",
    limit: int = 20,
    datasource_uid: str | None = None,
    spss: int = 3,
) -> dict:
    uid = _resolve_uid(datasource_uid)
    from_ms, to_ms = get_client().parse_range(from_time, to_time)
    payload = {
        "from": str(from_ms),
        "to": str(to_ms),
        "queries": [
            {
                "refId": "A",
                "datasource": {"type": "tempo", "uid": uid},
                "queryType": "traceql",
                "query": traceql,
                "limit": limit,
                "spss": spss,
                "tableType": "traces",
            }
        ],
    }
    try:
        response = get_client().request("POST", "/api/ds/query", json_body=payload)
        traces = _traces_from_frames(response if isinstance(response, dict) else {})
    except GrafanaError as exc:
        if exc.status not in (404, 405):
            raise
        params = {
            "q": traceql,
            "start": str(from_ms // 1000),
            "end": str(to_ms // 1000),
            "limit": limit,
            "spss": spss,
        }
        response = get_client().request(
            "GET",
            f"/api/datasources/proxy/uid/{uid}/api/search",
            params=params,
            headers=LLM_ACCEPT,
        )
        traces = _traces_from_proxy(response)
    return {"traces": traces[: max(1, min(limit, 100))]}


def _flatten_spans(payload: dict) -> tuple[list[dict], dict]:
    resource_attributes: dict = {}
    spans: list[dict] = []
    batches = payload.get("data", payload.get("batches", payload.get("traces", []))) or []
    for batch in batches:
        if not isinstance(batch, dict):
            continue
        for attribute in batch.get("resource", {}).get("attributes", []) or []:
            resource_attributes[attribute.get("key")] = attribute.get("value", {}).get("stringValue")
        groups = batch.get("scopeSpans", batch.get("instrumentationLibrarySpans", [])) or []
        for group in groups:
            spans.extend(group.get("spans", []) or [])
    return spans, resource_attributes


def get_trace(
    trace_id: str,
    from_time: str = "now-1h",
    to_time: str = "now",
    datasource_uid: str | None = None,
) -> dict:
    uid = _resolve_uid(datasource_uid)
    from_ms, to_ms = get_client().parse_range(from_time, to_time)
    response = get_client().request(
        "GET",
        f"/api/datasources/proxy/uid/{uid}/api/traces/{trace_id}",
        params={"start": str(from_ms // 1000), "end": str(to_ms // 1000)},
        headers=LLM_ACCEPT,
    )
    spans, resource = _flatten_spans(response if isinstance(response, dict) else {})
    roots = [span for span in spans if not span.get("parentSpanId")]
    root = roots[0] if roots else (spans[0] if spans else {})
    start_ns = int(root.get("startTimeUnixNano", 0))
    end_ns = int(root.get("endTimeUnixNano", 0))

    def span_service(span: dict) -> str | None:
        for attribute in span.get("attributes", []) or []:
            if attribute.get("key") == "service.name":
                return attribute.get("value", {}).get("stringValue")
        return resource.get("service.name")

    slowest = sorted(
        spans,
        key=lambda span: int(span.get("endTimeUnixNano", 0)) - int(span.get("startTimeUnixNano", 0)),
        reverse=True,
    )[:5]
    return {
        "trace_id": trace_id,
        "root_service": span_service(root),
        "duration_ms": (end_ns - start_ns) / NS_PER_MS,
        "error_spans": [
            {
                "span_id": span.get("spanId"),
                "name": span.get("name"),
                "status": (span.get("status") or {}).get("code"),
            }
            for span in spans
            if (span.get("status") or {}).get("code") in (2, "STATUS_CODE_ERROR")
        ],
        "slowest_spans": [
            {
                "span_id": span.get("spanId"),
                "name": span.get("name"),
                "service": span_service(span),
                "duration_ms": (
                    int(span.get("endTimeUnixNano", 0)) - int(span.get("startTimeUnixNano", 0))
                )
                / NS_PER_MS,
            }
            for span in slowest
        ],
    }


def tempo_attribute_values(
    name: str,
    datasource_uid: str | None = None,
    from_time: str = "now-1h",
    to_time: str = "now",
) -> list[str]:
    uid = _resolve_uid(datasource_uid)
    from_ms, to_ms = get_client().parse_range(from_time, to_time)
    response = get_client().request(
        "GET",
        f"/api/datasources/proxy/uid/{uid}/api/search/tag/{name}/values",
        params={"start": str(from_ms // 1000), "end": str(to_ms // 1000)},
        headers=LLM_ACCEPT,
    ) or {}
    values = response.get("tagValues", response.get("values", [])) if isinstance(response, dict) else []
    return [
        value.get("value") if isinstance(value, dict) else value
        for value in values
    ]


def correlate_trace_logs(
    trace_id: str,
    from_time: str = "now-1h",
    to_time: str = "now",
    loki_datasource_uid: str | None = None,
    limit: int = 50,
) -> dict:
    result = search_logs(
        f'{{job=~".+"}} |= "{trace_id}"',
        from_time=from_time,
        to_time=to_time,
        limit=limit,
        datasource_uid=loki_datasource_uid,
    )
    return {**result, "trace_id": trace_id}


def register(mcp) -> None:
    mcp.tool(name="search_traces", annotations=READ_ONLY)(search_traces)
    mcp.tool(name="get_trace", annotations=READ_ONLY)(get_trace)
    mcp.tool(name="tempo_attribute_values", annotations=READ_ONLY)(tempo_attribute_values)
    mcp.tool(name="correlate_trace_logs", annotations=READ_ONLY)(correlate_trace_logs)
