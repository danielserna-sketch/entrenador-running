"""Login inicial en Garmin Connect (una sola vez, en tu computador).

Pide usuario, contraseña y código MFA si aplica, y guarda los tokens en AWS Secrets Manager
para que las Lambdas no tengan que volver a iniciar sesión. Tu contraseña no se guarda.

Uso:
    uv run python scripts/garmin_login.py                # guarda en Secrets Manager
    uv run python scripts/garmin_login.py --file tokens.json   # o en un archivo local (pruebas)

Repite este script si llega la alerta "Garmin: hay que renovar la sesión".
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from garminconnect import Garmin


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="Guardar tokens en este archivo en vez de Secrets Manager")
    args = parser.parse_args()

    email = input("Correo de Garmin: ").strip()
    password = getpass.getpass("Contraseña de Garmin: ")
    api = Garmin(email, password, prompt_mfa=lambda: input("Código MFA: ").strip())
    api.login()
    tokens = api.client.dumps()
    print(f"Sesión iniciada como {api.get_full_name()}")

    if args.file:
        path = Path(args.file)
        path.write_text(tokens)
        path.chmod(0o600)
        print(f"Tokens guardados en {path} (no lo subas a git).")
        return

    from coach.garmin.token_store import save_tokens

    save_tokens(tokens)
    print("Tokens guardados en Secrets Manager.")


if __name__ == "__main__":
    main()
