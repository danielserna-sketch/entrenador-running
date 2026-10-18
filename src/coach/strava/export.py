"""Importa el archivo exportado de Strava (Configuración → Mi cuenta → Descargar o eliminar tu cuenta).

La carpeta trae `activities.csv` (en el idioma de la cuenta, con fechas en UTC) y un archivo por
actividad en `activities/` (.fit.gz, .gpx, .tcx.gz…). Del CSV salen los resúmenes y de los
tracks se calculan los mejores esfuerzos por distancia (como los best_efforts de Strava) y los
splits por km. El resultado tiene el mismo esquema que `mappers.map_detail`, así que se guarda
en STRAVA_ACTIVITY# y alimenta los mismos récords y tendencias que la API.
"""

from __future__ import annotations

import csv
import gzip
import logging
import math
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from coach.garmin.mappers import _speed_to_pace
from coach.strava.mappers import EFFORT_KM

log = logging.getLogger(__name__)

# Columnas del CSV por posición: los nombres cambian con el idioma y varios se repiten.
COL = {
    "id": 0, "date": 1, "name": 2, "type": 3, "description": 4, "filename": 12,
    "elapsed_s": 15, "moving_s": 16, "distance_m": 17, "max_speed": 18, "avg_speed": 19,
    "elev_gain": 20, "max_cadence": 28, "avg_cadence": 29, "max_hr": 30, "avg_hr": 31,
    "avg_watts": 33, "calories": 34, "relative_effort": 37, "weighted_watts": 46,
    "total_steps": 85, "training_load": 88, "gap_speed": 90,
}
HEADER_CHECK = {0: ("Activity ID", "ID de actividad"), 3: ("Activity Type", "Tipo de actividad")}

RUN_TYPES = {"carrera", "run", "trail run", "carrera por montaña", "virtual run", "carrera virtual"}
SPORTS = {"bicicleta": "ride", "ride": "ride", "caminata": "walk", "walk": "walk", "natación": "swim",
          "swim": "swim", "entrenamiento con pesas": "weight_training", "weight training": "weight_training",
          "entrenamiento": "workout", "workout": "workout", "senderismo": "hike", "hike": "hike"}

MONTHS = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sept": 9,
          "sep": 9, "oct": 10, "nov": 11, "dic": 12, "jan": 1, "apr": 4, "aug": 8, "dec": 12}

Track = list[tuple[float, float, int | None]]  # (segundos desde el inicio, metros acumulados, FC)


# ---------------------------------------------------------------------------- CSV
def _num(value: str) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value.replace(",", ".")) if value.count(",") == 1 and "." not in value else float(value)
    except ValueError:
        return None


def parse_date(value: str) -> datetime:
    """'6 oct 2026, 22:47:18' (es) o 'Oct 6, 2026, 10:47:18 PM' (en), en UTC."""
    value = value.strip()
    try:
        return datetime.strptime(value, "%b %d, %Y, %I:%M:%S %p").replace(tzinfo=UTC)
    except ValueError:
        pass
    day, month, rest = value.replace(",", "").split(" ", 2)
    year, clock = rest.split(" ")
    h, m, s = (int(x) for x in clock.split(":"))
    return datetime(int(year), MONTHS[month.lower().rstrip(".")], int(day), h, m, s, tzinfo=UTC)


def read_csv(folder: Path) -> list[list[str]]:
    with open(folder / "activities.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    for idx, names in HEADER_CHECK.items():
        if header[idx] not in names:
            raise ValueError(f"activities.csv con formato inesperado: columna {idx} = {header[idx]!r}")
    return rows[1:]


def map_row(row: list[str], tz: ZoneInfo) -> dict:
    def get(key: str) -> str:
        i = COL[key]
        return row[i] if i < len(row) else ""

    start_utc = parse_date(get("date"))
    start_local = start_utc.astimezone(tz).replace(tzinfo=None)
    type_name = get("type").strip()
    is_run = type_name.lower() in RUN_TYPES
    cadence = _num(get("avg_cadence"))
    distance_m = _num(get("distance_m")) or 0
    moving = _num(get("moving_s")) or _num(get("elapsed_s"))
    return {
        "activity_id": get("id"),
        "source": "strava_export",
        "name": get("name"),
        "date": start_local.date().isoformat(),
        "start_local": start_local.isoformat(),
        "start_utc": start_utc.isoformat(),
        "type": type_name,
        "sport": "running" if is_run else SPORTS.get(type_name.lower(), type_name.lower() or "other"),
        "distance_km": round(distance_m / 1000, 2),
        "duration_s": int(moving) if moving else None,
        "elapsed_s": int(_num(get("elapsed_s")) or 0) or None,
        "avg_pace_s_per_km": _speed_to_pace(_num(get("avg_speed"))),
        "ngp_s_per_km": _speed_to_pace(_num(get("gap_speed"))),
        "avg_hr": _num(get("avg_hr")),
        "max_hr": _num(get("max_hr")),
        # Strava reporta zancadas de una pierna por minuto en carrera.
        "avg_cadence_spm": round(cadence * 2, 1) if cadence and is_run else cadence,
        "elevation_gain_m": _num(get("elev_gain")),
        "avg_power_w": _num(get("avg_watts")),
        "norm_power_w": _num(get("weighted_watts")),
        "calories": _num(get("calories")),
        "suffer_score": _num(get("relative_effort")),
        "strava_training_load": _num(get("training_load")),
        "workout_type": None,
        "description": get("description") or None,
        "file": get("filename") or None,
    }


# ---------------------------------------------------------------------------- tracks
def _open(path: Path):
    return gzip.open(path, "rb") if path.suffix == ".gz" else open(path, "rb")


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _read_fit(path: Path) -> Track:
    import fitdecode

    points: Track = []
    t0 = None
    with _open(path) as fh, fitdecode.FitReader(fh) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage) or frame.name != "record":
                continue
            ts = frame.get_value("timestamp", fallback=None)
            dist = frame.get_value("distance", fallback=None)
            if ts is None or dist is None:
                continue
            t0 = t0 or ts
            points.append(((ts - t0).total_seconds(), float(dist), frame.get_value("heart_rate", fallback=None)))
    return points


def _read_gpx(path: Path) -> Track:
    points: Track = []
    t0 = prev = None
    dist = 0.0
    with _open(path) as fh:
        for _, el in ET.iterparse(fh):
            if not el.tag.endswith("trkpt"):
                continue
            time_el = next((c for c in el if c.tag.endswith("time")), None)
            if time_el is None:
                el.clear()
                continue
            ts = datetime.fromisoformat(time_el.text)
            lat, lon = float(el.get("lat")), float(el.get("lon"))
            hr = next((int(float(h.text)) for h in el.iter() if h.tag.endswith("}hr")), None)
            if prev:
                dist += _haversine(prev[0], prev[1], lat, lon)
            prev = (lat, lon)
            t0 = t0 or ts
            points.append(((ts - t0).total_seconds(), dist, hr))
            el.clear()
    return points


def read_track(path: Path) -> Track:
    name = path.name.lower()
    if ".fit" in name:
        return _read_fit(path)
    if ".gpx" in name:
        return _read_gpx(path)
    raise ValueError(f"Formato no soportado: {path.name}")


def best_efforts(track: Track) -> dict[str, float]:
    """Menor tiempo transcurrido para cubrir cada distancia (ventana deslizante sobre el track).

    El inicio de la ventana se interpola entre muestras para no favorecer relojes que graban cada
    pocos segundos. Usa tiempo transcurrido, como los best_efforts de Strava.
    """
    if len(track) < 2:
        return {}
    times = [p[0] for p in track]
    dists = [p[1] for p in track]
    total = dists[-1] - dists[0]
    out = {}
    for key, km in EFFORT_KM.items():
        target = km * 1000
        if target > total:
            continue
        best = math.inf
        i = 0
        for j in range(1, len(track)):
            # avanza el inicio mientras la ventana siga cubriendo la distancia
            while i + 1 < j and dists[j] - dists[i + 1] >= target:
                i += 1
            covered = dists[j] - dists[i]
            if covered < target:
                continue
            # interpola el punto exacto de inicio dentro del tramo [i, i+1]
            seg = dists[i + 1] - dists[i]
            frac = (covered - target) / seg if seg > 0 else 0
            start_t = times[i] + frac * (times[i + 1] - times[i])
            best = min(best, times[j] - start_t)
        if best < math.inf:
            out[key] = round(best, 1)
    return out


def km_splits(track: Track) -> list[dict]:
    splits = []
    if len(track) < 2:
        return splits
    next_km, last_t, hrs = 1000.0, 0.0, []
    for t, d, hr in track:
        if hr:
            hrs.append(hr)
        if d >= next_km:
            dur = t - last_t
            splits.append({"km": int(next_km // 1000), "distance_km": 1.0, "duration_s": round(dur),
                           "pace_s_per_km": round(dur, 1), "avg_hr": round(sum(hrs) / len(hrs)) if hrs else None})
            next_km, last_t, hrs = next_km + 1000, t, []
    return splits


# ---------------------------------------------------------------------------- import
def iter_activities(folder: Path, tz: ZoneInfo, with_tracks: bool = True) -> Iterator[dict]:
    """Recorre el archivo y entrega cada actividad normalizada; las carreras con track traen detalle."""
    for row in read_csv(folder):
        activity = map_row(row, tz)
        file = activity.pop("file")
        if activity["sport"] == "running" and with_tracks and file and (folder / file).exists():
            try:
                track = read_track(folder / file)
                efforts = best_efforts(track)
                activity["best_efforts"] = [
                    {"distance": k, "elapsed_s": v, "pace_s_per_km": round(v / EFFORT_KM[k], 1),
                     "date": activity["date"], "activity_id": activity["activity_id"]}
                    for k, v in efforts.items()
                ]
                activity["splits"] = km_splits(track)
                activity["detailed"] = True
            except Exception as e:  # noqa: BLE001
                log.warning("No pude leer %s: %s", file, e)
        yield activity
