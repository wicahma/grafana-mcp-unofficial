from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from client import GrafanaClient, GrafanaError

ENV_KEYS = {
    "base_url": "GRAFANA_URL",
    "token": "GRAFANA_SERVICE_ACCOUNT_TOKEN",
    "token_file": "GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE",
    "username": "GRAFANA_USERNAME",
    "password": "GRAFANA_PASSWORD",
}


def credentials_path() -> str:
    return os.environ.get("GRAFANA_CREDENTIALS_PATH") or os.path.expanduser(
        r"~/.omp/grafana_credentials.json"
    )


def load_credentials() -> dict:
    credentials: dict = {}
    path = credentials_path()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                stored = json.load(handle)
            if isinstance(stored, dict):
                credentials.update({k: v for k, v in stored.items() if isinstance(v, str)})
        except (OSError, ValueError) as exc:
            sys.stderr.write(f"Warning: failed reading Grafana credentials file: {exc}\n")

    for key, env_name in ENV_KEYS.items():
        value = os.environ.get(env_name)
        if value is not None and value.strip():
            credentials[key] = value

    return credentials


def build_client(**overrides) -> GrafanaClient:
    credentials = load_credentials()
    credentials.update({k: v for k, v in overrides.items() if v is not None})
    return GrafanaClient(
        credentials.get("base_url"),
        token=credentials.get("token"),
        token_file=credentials.get("token_file"),
        username=credentials.get("username"),
        password=credentials.get("password"),
    )


def verify(client: GrafanaClient) -> bool:
    try:
        user = client.request("GET", "/api/user")
        print(
            f"[AUTH_OK] Login berhasil sebagai: {user.get('name')} ({user.get('email')}) "
            f"- ID: {user.get('id')}"
        )
        return True
    except (GrafanaError, ValueError, OSError) as exc:
        print(f"[AUTH_FAILED] {exc}")
        return False


def save_credentials(data: dict) -> str:
    path = credentials_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print(f"Kredensial Grafana disimpan di {path}")
    return path


def cmd_check(args: argparse.Namespace) -> int:
    return 0 if verify(build_client()) else 1


def cmd_update(args: argparse.Namespace) -> int:
    data = load_credentials()
    data["base_url"] = args.base_url or data.get("base_url") or ""
    if not data["base_url"]:
        print("Grafana base URL wajib: berikan --base-url atau GRAFANA_URL.")
        return 1
    for key in ("token", "token_file", "username"):
        value = getattr(args, key)
        if value:
            data[key] = value.strip()

    password = args.password or data.get("password")
    has_token = data.get("token") or data.get("token_file")
    if password is None and not has_token:
        if not data.get("username"):
            data["username"] = input("Grafana username/email: ").strip()
        password = getpass.getpass("Grafana password: ")
    if password is not None:
        data["password"] = password

    client = GrafanaClient(
        data["base_url"],
        token=data.get("token"),
        token_file=data.get("token_file"),
        username=data.get("username"),
        password=data.get("password"),
    )
    if not verify(client):
        print("Kredensial tidak diverifikasi; tidak ada yang disimpan.")
        return 1
    save_credentials(data)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Kelola kredensial Grafana untuk MCP ini.")
    parser.add_argument("action", choices=["check", "update"])
    parser.add_argument("--base-url", help="URL Grafana (wajib via argumen, env, atau file kredensial)")
    parser.add_argument("--token", help="Service account token")
    parser.add_argument("--token-file", help="Path file yang berisi service account token")
    parser.add_argument("--username", help="Username / email Grafana")
    parser.add_argument("--password", help="Password Grafana (jika kosong, diminta via prompt)")
    args = parser.parse_args()
    if args.action == "check":
        return cmd_check(args)
    return cmd_update(args)


if __name__ == "__main__":
    sys.exit(main())
