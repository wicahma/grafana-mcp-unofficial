from __future__ import annotations

import argparse
import os
import sys

from mcp.server.fastmcp import FastMCP

from credential_manager import build_client
from tools import set_client
from tools import alerting, core, dashboards, datasources, logs, slow, traces


def main() -> int:
    parser = argparse.ArgumentParser(description="Custom Grafana MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.environ.get("GRAFANA_MCP_TRANSPORT", "stdio"),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8123)
    args = parser.parse_args()

    client = build_client()
    try:
        client.check_credentials()
    except Exception as exc:
        sys.stderr.write(f"ERROR: Grafana credentials unavailable: {exc}\n")
        return 1
    set_client(client)

    mcp = FastMCP("grafana-custom", host=args.host, port=args.port)
    for module in (core, dashboards, datasources, logs, traces, slow, alerting):
        module.register(mcp)

    try:
        mcp.run(transport=args.transport)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
