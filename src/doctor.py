from __future__ import annotations

import json
import sys

from client import GrafanaError
from credential_manager import build_client


def probe() -> dict:
    client = build_client()
    checks = [
        ("health", "/api/health", None),
        ("user", "/api/user", None),
        ("datasources", "/api/datasources", None),
        ("dashboard_search", "/api/search", {"type": "dash-db", "limit": 1}),
    ]
    report: dict = {}
    for name, path, params in checks:
        try:
            report[name] = client.request("GET", path, params=params)
        except (GrafanaError, ValueError, OSError) as exc:
            report[name] = {"error": str(exc)}

    datasources = report.get("datasources")
    report["datasource_types"] = (
        sorted({str(item.get("type", "")) for item in datasources}) if isinstance(datasources, list) else []
    )
    client.close()
    return report


def main() -> int:
    print(json.dumps(probe(), indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
