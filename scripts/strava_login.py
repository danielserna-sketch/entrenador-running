"""Conecta tu cuenta de Strava (una sola vez, en tu computador).

Antes: crea una app en https://www.strava.com/settings/api con
"Authorization Callback Domain" = localhost. Copia su Client ID y Client Secret.

El script abre el navegador para que autorices el acceso de solo lectura (actividades, también
las privadas, y perfil), recibe el código en http://localhost:8765 y guarda los tokens en AWS
Secrets Manager. Tu contraseña de Strava nunca pasa por aquí.

Uso:
    uv run python scripts/strava_login.py                    # guarda en Secrets Manager
    uv run python scripts/strava_login.py --file strava.json # o en un archivo local (pruebas)

Repite este script si llega la alerta "Strava: perdí el acceso a tu cuenta".
"""

from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PORT = 8765
SCOPE = "read,activity:read_all,profile:read_all"


def wait_for_code(expected_state: str) -> str:
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            params = parse_qs(urlparse(self.path).query)
            if params.get("state", [""])[0] != expected_state:
                self.send_response(400)
                self.end_headers()
                return
            result.update({k: v[0] for k, v in params.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Listo, ya puedes cerrar esta pestaña." if "code" in result else "No se autorizó el acceso."
            self.wfile.write(f"<p>{msg}</p>".encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("localhost", PORT), Handler)
    while not result:
        server.handle_request()
    server.server_close()
    if "code" not in result:
        sys.exit(f"Strava no autorizó el acceso: {result.get('error')}")
    granted = set(result.get("scope", "").split(","))
    if "activity:read_all" not in granted:
        print("Aviso: no autorizaste ver actividades privadas; solo se leerán las públicas.")
    return result["code"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="Guardar tokens en este archivo en vez de Secrets Manager")
    args = parser.parse_args()

    client_id = input("Client ID de tu app de Strava: ").strip()
    client_secret = getpass.getpass("Client Secret: ").strip()
    state = secrets.token_urlsafe(16)
    url = "https://www.strava.com/oauth/authorize?" + urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": f"http://localhost:{PORT}",
        "approval_prompt": "force", "scope": SCOPE, "state": state,
    })
    print(f"Abriendo el navegador para autorizar. Si no abre, entra a:\n{url}")
    webbrowser.open(url)
    code = wait_for_code(state)

    resp = httpx.post("https://www.strava.com/oauth/token", data={
        "client_id": client_id, "client_secret": client_secret, "code": code,
        "grant_type": "authorization_code",
    })
    resp.raise_for_status()
    data = resp.json()
    athlete = data.get("athlete") or {}
    print(f"Conectado como {athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip())
    tokens = {"client_id": client_id, "client_secret": client_secret, "refresh_token": data["refresh_token"],
              "access_token": data["access_token"], "expires_at": data["expires_at"]}

    if args.file:
        path = Path(args.file)
        path.write_text(json.dumps(tokens))
        path.chmod(0o600)
        print(f"Tokens guardados en {path} (no lo subas a git).")
        return

    from coach.strava.token_store import save_tokens

    save_tokens(tokens)
    print("Tokens guardados en Secrets Manager. El historial se descarga en las próximas horas.")


if __name__ == "__main__":
    main()
