"""Sondeo de solo lectura: imprime la forma (claves y tipos) de las respuestas de Garmin que usa el agente.

No imprime valores, así que puedes pegar la salida sin exponer datos personales.

Uso (con los tokens de `scripts/garmin_login.py --file tokens.json`):
    uv run python scripts/garmin_probe.py --file tokens.json

Sin --file lee los tokens de Secrets Manager, como las Lambdas.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from garminconnect import Garmin

from coach.garmin import token_store


def shape(value, depth: int = 0, max_depth: int = 3):
    """Describe la estructura sin valores: tipos y claves anidadas."""
    if isinstance(value, dict):
        if depth >= max_depth:
            return f"dict({len(value)} claves)"
        return {k: shape(v, depth + 1, max_depth) for k, v in value.items()}
    if isinstance(value, list):
        if not value:
            return "list(vacía)"
        return [f"list({len(value)} items)", shape(value[0], depth + 1, max_depth)]
    return type(value).__name__ if value is not None else "None"


def probe(name: str, fn) -> None:
    print(f"\n## {name}")
    try:
        print(shape(fn()))
    except Exception as e:  # noqa: BLE001
        print(f"ERROR {type(e).__name__}: {e}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="Archivo de tokens generado con garmin_login.py --file")
    args = parser.parse_args()
    # Cliente directo: UnofficialGarmin guarda los tokens rotados en Secrets Manager (producción).
    # Aquí solo se leen, así el sondeo no altera la sesión que usan las Lambdas.
    tokens = Path(args.file).read_text() if args.file else token_store.load_tokens()
    api = Garmin()
    api.login(tokenstore=tokens)
    today = date.today()
    start, end = today, today + timedelta(days=14)
    d = today.isoformat()

    probe("scheduled_workouts_by_period (próximos 14 días)",
          lambda: api.get_scheduled_workouts_by_period(start.isoformat(), end.isoformat()))
    probe("get_scheduled_workouts (mes actual)", lambda: api.get_scheduled_workouts(today.year, today.month))
    probe("get_max_metrics", lambda: api.get_max_metrics(d))
    probe("get_race_predictions", lambda: api.get_race_predictions())
    probe("get_training_status", lambda: api.get_training_status(d))
    probe("get_lactate_threshold", lambda: api.get_lactate_threshold(latest=True))
    probe("get_training_readiness", lambda: api.get_training_readiness(d))


if __name__ == "__main__":
    main()
