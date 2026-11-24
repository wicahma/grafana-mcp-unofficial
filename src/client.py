from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx


RETRYABLE_STATUS = {429, 502, 503, 504}
DS_CACHE_SECONDS = 300


class GrafanaError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


class GrafanaClient:
    """Minimal pooled HTTP client for the Grafana HTTP API."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        token: str | None = None,
        token_file: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 20.0,
    ):
        self.base_url = (base_url or os.environ.get("GRAFANA_URL") or "").rstrip("/")
        self.token = token
        self.token_file = token_file
        self.username = username
        self.password = password
        self.timeout = timeout
        self._http = httpx.Client(timeout=timeout)
        self._ds_cache: tuple[float, list[dict]] | None = None

    def check_credentials(self) -> None:
        if not self.base_url:
            raise GrafanaError(
                0,
                "Grafana base URL is required: set GRAFANA_URL or base_url in the local credentials file.",
            )
        self._resolve_auth()

    @staticmethod
    def _sanitize(text: str) -> str:
        text = re.sub(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?\S+", r"\1<redacted>", text)
        text = re.sub(r"(?i)(password\s*[:=]\s*)\S+", r"\1<redacted>", text)
        return text

    def _resolve_auth(self):
        token = (self.token or os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN") or "").strip()
        if token:
            return {"Authorization": f"Bearer {token}"}

        token_file = (self.token_file or os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE") or "").strip()
        if token_file:
            try:
                with open(token_file, encoding="utf-8") as handle:
                    file_token = handle.read().strip()
            except OSError as exc:
                raise GrafanaError(0, f"Cannot read Grafana token file: {exc}") from exc
            if file_token:
                return {"Authorization": f"Bearer {file_token}"}

        username = (self.username or os.environ.get("GRAFANA_USERNAME") or "").strip()
        password = self.password
        if password is None:
            password = os.environ.get("GRAFANA_PASSWORD") or ""
        if username and password:
            return ("basic", (username, password))

        raise GrafanaError(
            0,
            "No Grafana credentials found. Provide GRAFANA_SERVICE_ACCOUNT_TOKEN, "
            "GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE, or GRAFANA_USERNAME + GRAFANA_PASSWORD "
            "(also supported in ~/.omp/grafana_credentials.json).",
        )

    @staticmethod
    def _validate_path(path: str) -> None:
        if not (path.startswith("/api/") or path.startswith("/apis/")):
            raise ValueError("path must start with /api/ or /apis/")
        if "://" in path or ".." in path or "//" in path[1:]:
            raise ValueError("path must not contain '://', '..' or '//'")

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        headers: dict | None = None,
    ) -> Any:
        self._validate_path(path)
        auth = self._resolve_auth()
        request_headers = dict(headers or {})
        basic_auth = None
        if isinstance(auth, tuple):
            basic_auth = auth[1]
        else:
            request_headers.update(auth)

        url = self.base_url + path
        for attempt in range(3):
            try:
                response = self._http.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=request_headers,
                    auth=basic_auth,
                )
            except httpx.HTTPError as exc:
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise GrafanaError(0, self._sanitize(str(exc))) from exc

            if response.status_code in RETRYABLE_STATUS and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise GrafanaError(response.status_code, self._sanitize(response.text[:1000]))
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError:
                return response.text

    @staticmethod
    def parse_range(from_time: str = "now-15m", to_time: str = "now") -> tuple[int, int]:
        now_ms = int(time.time() * 1000)

        def parse(value: str) -> int:
            text = str(value).strip()
            if text == "now":
                return now_ms
            relative = re.fullmatch(r"now-(\d+)([mhdw])", text)
            if relative:
                multiplier = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
                return now_ms - int(relative.group(1)) * multiplier[relative.group(2)]
            if re.fullmatch(r"\d{10,13}", text):
                number = int(text)
                return number * 1000 if number < 100_000_000_000 else number
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return int(parsed.timestamp() * 1000)
            except ValueError as exc:
                raise ValueError(f"Unsupported time value: {value}") from exc

        start, end = parse(from_time), parse(to_time)
        if start >= end:
            raise ValueError("from_time must be earlier than to_time")
        return start, end

    def list_datasources(self) -> list[dict]:
        now = time.monotonic()
        if self._ds_cache and now - self._ds_cache[0] < DS_CACHE_SECONDS:
            return self._ds_cache[1]
        data = self.request("GET", "/api/datasources")
        datasources = data if isinstance(data, list) else []
        self._ds_cache = (now, datasources)
        return datasources

    def resolve_datasource(self, uid: str | None, type_hint: str | None) -> dict:
        datasources = self.list_datasources()
        available = sorted({str(ds.get("type", "")) for ds in datasources})

        if uid:
            for datasource in datasources:
                if datasource.get("uid") == uid:
                    if type_hint and str(datasource.get("type", "")).lower() != type_hint.lower():
                        raise GrafanaError(
                            0,
                            f"Datasource {uid} has type {datasource.get('type')}, expected {type_hint}.",
                        )
                    return datasource
            raise GrafanaError(0, f"Datasource UID not found: {uid}")

        if type_hint:
            for datasource in datasources:
                if str(datasource.get("type", "")).lower() == type_hint.lower():
                    return datasource
            raise GrafanaError(
                0,
                f"No '{type_hint}' datasource found. Available types: {', '.join(available) or 'none'}.",
            )

        if datasources:
            return datasources[0]
        raise GrafanaError(0, "No datasources configured on this Grafana instance.")

    def close(self) -> None:
        self._http.close()
