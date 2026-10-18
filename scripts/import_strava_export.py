"""Carga el archivo exportado de Strava en DynamoDB para que el entrenador conozca tu historial.

Cómo conseguir el archivo: Strava → Configuración → Mi cuenta → Descargar o eliminar tu cuenta →
Solicitar tu archivo. Descomprime el .zip (necesitas `activities.csv` y la carpeta `activities/`).

Uso:
    uv run python scripts/import_strava_export.py Contexto --dry-run   # solo muestra el resumen
    uv run python scripts/import_strava_export.py Contexto             # carga en AWS

Se puede repetir con un archivo más nuevo: las actividades se sobrescriben por id, sin duplicar.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def stack_outputs(stack: str, region: str) -> dict[str, str]:
    import boto3

    desc = boto3.client("cloudformation", region_name=region).describe_stacks(StackName=stack)
    return {o["OutputKey"]: o["OutputValue"] for o in desc["Stacks"][0].get("Outputs", [])}


def fmt(seconds: float) -> str:
    m, s = divmod(round(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", nargs="?", default="Contexto", help="Carpeta del archivo descomprimido")
    parser.add_argument("--dry-run", action="store_true", help="No escribe en AWS")
    parser.add_argument("--stack", default="EntrenadorRunning")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--timezone", default="America/Bogota")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if not args.dry_run:
        outputs = stack_outputs(args.stack, args.region)
        os.environ.update({"TABLE_NAME": outputs["TableName"], "BUCKET_NAME": outputs["BucketName"],
                           "AWS_DEFAULT_REGION": args.region, "COACH_TIMEZONE": args.timezone})

    from zoneinfo import ZoneInfo

    from coach.analytics import history
    from coach.garmin.sync import enrich_activity
    from coach.strava.export import iter_activities

    repo = None
    zones = None
    if not args.dry_run:
        from coach.storage.repo import Repo

        repo = Repo()
        zones = repo.get_zones()

    folder = Path(args.folder)
    print(f"Leyendo {folder / 'activities.csv'} y los tracks (puede tardar un minuto)…")
    activities = []
    for activity in iter_activities(folder, ZoneInfo(args.timezone)):
        enrich_activity(activity, zones)
        activities.append(activity)
        if repo:
            repo.save_strava_activity(activity)

    today = date.today()
    runs = [a for a in activities if a["sport"] == "running"]
    records = history.personal_records(activities, today)
    summary = history.history_summary(activities)
    print(f"\n{len(activities)} actividades ({len(runs)} carreras, "
          f"{sum(1 for a in runs if a.get('detailed'))} con track) "
          f"del {summary.get('first_run')} al {summary.get('last_run')}, {summary.get('total_km')} km.")
    print("\nRécords (de siempre / últimos 12 meses):")
    for key, r in records["distances"].items():
        recent = r["last_12_months"]
        print(f"  {key:<9} {fmt(r['all_time']['time_s']):>8}  {r['all_time']['date']}"
              f"   {fmt(recent['time_s']) if recent else '—':>8}")

    if repo:
        repo.save_records(records)
        repo.put("META#strava_export", {"imported_on": today.isoformat(), "activities": len(activities),
                                        "runs": len(runs), "last_activity": summary.get("last_run")})
        print("\nListo: historial y récords cargados. El entrenador ya los puede consultar.")
    else:
        print("\n(--dry-run: no se escribió nada en AWS)")


if __name__ == "__main__":
    main()
