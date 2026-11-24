# grafana-mcp-unofficial

Connector MCP buatan sendiri untuk Grafana. URL instance tidak disimpan di repo;
ditentukan lewat `GRAFANA_URL` atau file kredensial lokal. Tidak membungkus biner MCP orang lain:
seluruh tool dibangun di atas `httpx` + FastMCP dan berbicara langsung ke HTTP API Grafana.

## Instalasi

```bash
pip install -r requirements.txt
```

Python 3.12+, `mcp>=1.29,<2`, `httpx>=0.28,<1`.

## Kredensial

Simpan kredensial **hanya** di file lokal di bawah `~/.omp/`, bukan di repo ini.

```bash
python src/credential_manager.py update --base-url <grafana-url> --username <email> --password <password>
python src/credential_manager.py check
```

`update` menyimpan JSON ke `~/.omp/grafana_credentials.json` dengan izin `0600` (best-effort)
dan memverifikasi lewat `GET /api/user` sebelum menyimpan. Jika `--password` tidak diberikan,
password diminta lewat prompt. Service account token lebih disarankan daripada password:

```bash
python src/credential_manager.py update --token <service-account-token>
python src/credential_manager.py update --token-file /path/ke/token.txt
```

Prioritas autentikasi: `GRAFANA_SERVICE_ACCOUNT_TOKEN` → isi `GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE`
(dibaca ulang setiap request agar rotasi token aktif) → `GRAFANA_USERNAME` + `GRAFANA_PASSWORD`
(HTTP Basic). File token juga bisa ditaruh di `token_file` pada file kredensial JSON.

> Simpan kredensial hanya di file lokal di luar repo dan jangan pernah commit.
> Service account token lebih disarankan daripada password.

### Environment variable

| Nama | Fungsi |
| --- | --- |
| `GRAFANA_URL` | Base URL Grafana (wajib, atau lewat file kredensial) |
| `GRAFANA_SERVICE_ACCOUNT_TOKEN` | Token service account |
| `GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE` | Path file token; dibaca ulang tiap request |
| `GRAFANA_USERNAME` | Email/user Grafana untuk Basic auth |
| `GRAFANA_PASSWORD` | Password untuk Basic auth |
| `GRAFANA_CREDENTIALS_PATH` | Path file kredensial JSON (default `~/.omp/grafana_credentials.json`) |
| `GRAFANA_MCP_TRANSPORT` | `stdio` (default) atau `streamable-http` |

## Konfigurasi MCP

Tambahkan entri berikut ke `~/.omp/mcp.json` atau `~/.gemini/config/mcp_config.json`
(samping entri yang sudah ada):

```json
"grafana": {
  "command": "python",
  "args": ["<path-ke-repo>\\src\\runner.py"],
  "env": {
    "GRAFANA_CREDENTIALS_PATH": "<path-ke-file-kredensial-lokal>"
  }
}
```

Manual:

```bash
python src/runner.py --transport stdio
python src/runner.py --transport streamable-http --host 127.0.0.1 --port 8123
```

## Tool

Grup | Tool
--- | --- | ---
Inti | `grafana_health`, `grafana_whoami`, `grafana_api`, `search_dashboards`, `search_folders`
Dashboard | `get_dashboard`, `get_dashboard_summary`, `get_dashboard_panel_queries`, `list_dashboard_versions`, `create_dashboard`, `delete_dashboard`, `create_folder`
Datasource | `list_datasources`, `get_datasource`, `datasource_health`, `create_datasource`, `update_datasource`
Log | `loki_label_names`, `loki_label_values`, `search_logs`, `log_context`, `log_count`
Trace | `search_traces`, `get_trace`, `tempo_attribute_values`, `correlate_trace_logs`
Slow API | `query_prometheus`, `query_loki_metric`, `find_slow_requests`
Alerting | `list_alert_rules`, `get_alert_rule`, `list_contact_points`, `list_alert_groups`

`grafana_api` adalah jalur passthrough ke seluruh endpoint `/api/**` dan `/apis/**`,
termasuk write/delete yang tidak punya tool kurasi. Semua method selain GET/HEAD wajib
`confirm=True`; path divalidasi agar tidak bisa dipakai untuk SSRF ke host lain.

Tool tulis (`create_dashboard`, `delete_dashboard`, `create_folder`, `create_datasource`,
`update_datasource`) juga wajib `confirm=True`. Annotation `readOnlyHint` / `destructiveHint`
dikirim bersama definisi tool.

## Efisiensi pencarian log

`search_logs` memecah rentang menjadi jendela 15 menit dari terbaru ke terlama,
meminta `limit + 1` baris dengan `direction=backward`, dan berhenti setelah `limit`
baris terkumpul. Rentang dibatasi maksimal 24 jam per panggilan; selector stream wajib
ada dan filter regex ditolak kecuali `allow_regex=True`. Setiap request memakai header
`X-Query-Tags: Source=grafana-custom-mcp` untuk atribusi biaya query. Hasil memuat
`stats.totalBytesProcessed`, `windows_scanned`, dan `truncated` agar biaya scan terlihat.
Gunakan `before` (RFC3339 / epoch ns) untuk pagination dari entri tertua.

## Verifikasi

```bash
python src/credential_manager.py check
python src/doctor.py
python -m unittest discover -s tests -v
```

`doctor.py` melakukan probe read-only ke `/api/health`, `/api/user`, `/api/datasources`,
dan `/api/search` lalu mencetak ringkasan JSON.
