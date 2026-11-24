from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from client import GrafanaClient  # noqa: E402
from tools import set_client  # noqa: E402
from tools.core import grafana_api  # noqa: E402
from tools.dashboards import create_dashboard  # noqa: E402
from tools.logs import search_logs  # noqa: E402
from tools.slow import find_slow_requests  # noqa: E402


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.client = GrafanaClient("https://example.test", token="test-token")
        set_client(self.client)
        self.patcher = patch.object(GrafanaClient, "request", autospec=True)
        self.mock_request = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(set_client, None)

    def test_search_logs_requests_backward_window_and_caps_results(self):
        entries = [
            ["4000000000000000000", "fourth"],
            ["3000000000000000000", "third"],
            ["2000000000000000000", "second"],
            ["1000000000000000000", "first"],
        ]

        def fake(instance, method, path, *, params=None, json_body=None, headers=None):
            if path == "/api/datasources":
                return [{"uid": "loki-1", "type": "loki", "name": "Loki"}]
            return {
                "stats": {"summary": {"totalBytesProcessed": 4096}},
                "data": {"result": [{"metric": {"job": "api"}, "values": entries}]},
            }

        self.mock_request.side_effect = fake
        result = search_logs('{job="api"}', from_time="now-5m", limit=3)
        calls = [call for call in self.mock_request.call_args_list if "query_range" in call.args[2]]
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].args[2].startswith("/api/datasources/proxy/uid/loki-1/loki/api/v1/"))
        self.assertEqual(calls[0].kwargs["params"]["direction"], "backward")
        self.assertEqual(calls[0].kwargs["params"]["limit"], 4)
        self.assertEqual(len(result["entries"]), 3)
        self.assertTrue(result["truncated"])
        timestamps = [int(item["timestamp_ns"]) for item in result["entries"]]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))
        self.assertEqual(result["stats"]["totalBytesProcessed"], 4096)

    def test_search_logs_rejects_missing_selector_and_regex(self):
        with self.assertRaises(ValueError):
            search_logs('job="api"')
        with self.assertRaises(ValueError):
            search_logs('{job="api"} |=~ "foo"')
        self.mock_request.assert_not_called()

    def test_find_slow_requests_prometheus_percentile_and_threshold(self):
        def fake(instance, method, path, *, params=None, json_body=None, headers=None):
            if path == "/api/datasources":
                return [{"uid": "prom-1", "type": "prometheus", "name": "Prometheus"}]
            return {
                "results": {
                    "A": {
                        "frames": [
                            {
                                "schema": {"fields": [{"name": "Time"}, {"name": "Value", "labels": {"route": "/fast"}}]},
                                "data": {"values": [[1, 2], [100, 200]]},
                            },
                            {
                                "schema": {"fields": [{"name": "Time"}, {"name": "Value", "labels": {"route": "/slow"}}]},
                                "data": {"values": [[1, 2, 3, 4], [600, 700, 800, 900]]},
                            },
                        ]
                    }
                }
            }

        self.mock_request.side_effect = fake
        result = find_slow_requests(source="prometheus", threshold_ms=500, percentile=0.95)
        self.assertEqual(result["source"], "prometheus")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["route"], "/slow")
        self.assertEqual(result["items"][0]["p95_ms"], 900)

    def test_grafana_api_requires_confirm_for_write(self):
        with self.assertRaises(ValueError):
            grafana_api("DELETE", "/api/dashboards/uid/x", confirm=False)
        self.mock_request.assert_not_called()

    def test_create_dashboard_posts_requested_title(self):
        self.mock_request.return_value = {"uid": "dash-1", "title": "My Dashboard"}
        create_dashboard("My Dashboard", uid="dash-1", panels=[], confirm=True)
        calls = [call for call in self.mock_request.call_args_list if call.args[2] == "/api/dashboards/db"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[1], "POST")
        self.assertEqual(calls[0].kwargs["json_body"]["dashboard"]["title"], "My Dashboard")

    def test_client_rejects_ssrf_path(self):
        with self.assertRaises(ValueError):
            GrafanaClient._validate_path("https://evil.test/api/health")
        with self.assertRaises(ValueError):
            GrafanaClient._validate_path("/api/../apis/foo")
        self.mock_request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
