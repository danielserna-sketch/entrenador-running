"""Sincroniza Strava → DynamoDB: backfill de todo el historial que se puede reanudar y, después,
incremental. Respeta los límites de lectura de Strava guardando un cursor en META#strava_sync.

1. Listar: pagina /athlete/activities desde `listed_until` (orden ascendente) y guarda los resúmenes.
   Las carreras quedan en `pending` para pedir su detalle.
2. Detalle: por cada carrera pendiente trae best_efforts y splits (una llamada por actividad).
Se detiene cuando queda poco presupuesto de la ventana de 15 min o del día, o cuando se acaba el
tiempo de la Lambda, y continúa en la siguiente invocación.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime

from coach.analytics import history
from coach.garmin.sync import enrich_activity
from coach.storage.repo import Repo
from coach.strava import mappers
from coach.strava.client import StravaClient, StravaRateLimited

log = logging.getLogger(__name__)

PER_PAGE = 200
MIN_FREE_15MIN = 0.2
MIN_FREE_DAY = 0.1


def _epoch(iso_utc: str | None) -> int:
    return int(datetime.fromisoformat(iso_utc).timestamp()) if iso_utc else 0


def _garmin_id(external_id: str | None) -> str | None:
    """Las actividades subidas por Garmin traen external_id tipo 'garmin_ping_123456789'."""
    m = re.match(r"garmin_\w*?_?(\d+)", external_id or "")
    return m.group(1) if m else None


def _has_budget(client: StravaClient) -> bool:
    free_15, free_day = client.budget_left()
    return free_15 > MIN_FREE_15MIN and free_day > MIN_FREE_DAY


def _prepare(activity: dict, zones: dict | None) -> dict:
    activity["garmin_activity_id"] = _garmin_id(activity.get("external_id"))
    return enrich_activity(activity, zones)


def sync(client: StravaClient, repo: Repo, today: date, max_seconds: float = 480) -> dict:
    deadline = time.monotonic() + max_seconds
    state = repo.get_strava_sync()
    pending: list[str] = list(state.get("pending") or [])  # "<fecha>#<id>"
    zones = repo.get_zones()
    listed = detailed = 0
    stopped = None

    def out_of_budget() -> str | None:
        if time.monotonic() > deadline:
            return "tiempo"
        if not _has_budget(client):
            return "límite de Strava"
        return None

    try:
        # ---- 1. listar ---------------------------------------------------------------
        listing_done = False
        while not (stopped := out_of_budget()):
            page = client.list_activities(after=int(state.get("listed_until") or 0), per_page=PER_PAGE)
            for raw in page:
                activity = _prepare(mappers.map_activity(raw), zones)
                is_new = repo.save_strava_activity(activity)
                listed += 1
                key = f"{activity['date']}#{activity['activity_id']}"
                if activity["sport"] == "running" and is_new and key not in pending:
                    pending.append(key)
                state["listed_until"] = max(int(state.get("listed_until") or 0), _epoch(raw.get("start_date")))
            if len(page) < PER_PAGE:
                listing_done = True
                break

        # ---- 2. detalle ------------------------------------------------------------------
        while pending and not (stopped := out_of_budget()):
            day, activity_id = pending[0].split("#", 1)
            raw = client.activity(activity_id)
            activity = _prepare(mappers.map_detail(raw), zones)
            repo.save_strava_activity(activity)
            repo.put_object(f"raw/strava/activities/{day}/{activity_id}.json", json.dumps(raw, default=str))
            pending.pop(0)
            detailed += 1
    except StravaRateLimited as e:
        log.warning("Strava: límite alcanzado, se continúa en la próxima corrida: %s", e)
        stopped = "límite de Strava"
    finally:
        state["pending"] = pending
        if not state.get("backfill_done") and listing_done and not pending:
            state["backfill_done"] = True
            state["backfill_completed_on"] = today.isoformat()
        state["last_run"] = datetime.now().isoformat(timespec="seconds")
        repo.save_strava_sync(state)

    if listed or detailed:
        records = history.personal_records(repo.strava_activities(), today)
        repo.save_records(records)

    return {"listed": listed, "detailed": detailed, "pending": len(pending),
            "backfill_done": bool(state.get("backfill_done")), "stopped": stopped}
