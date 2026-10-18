"""Predicción de tiempos de carrera combinando varios métodos, con rango y confianza.

Métodos:
- Riegel (1981): T2 = T1 × (D2/D1)^k. Bien calibrado hasta media maratón; en maratón suele ser optimista
  para corredores recreativos (Vickers & Vertosick 2016), por eso se usa un exponente mayor (SUPUESTO).
- VDOT (Daniels-Gilbert): consumo de oxígeno estimado desde una marca y su equivalente en otra distancia.
  Muy preciso en corredores rápidos y peor en maratones de más de 4 h (Valencia 2022-23, Frontiers 2025).
- Tanda (2011): ritmo de maratón desde los km semanales y el ritmo medio de 8 semanas de entrenamiento.
- Garmin (Firstbeat): desde el VO2max; útil como tendencia, optimista en media y maratón.
- Velocidad crítica: solo informativa (D′ es ruidoso y su valor predictivo para carreras es débil).

Todo ajuste y peso es SUPUESTO salvo las fórmulas publicadas. Ver rules.py.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from coach.methodology import rules

DISTANCE_KM = {"mile": 1.6093, "2mile": 3.2187, "5k": 5.0, "10k": 10.0, "15k": 15.0, "10mile": 16.0934,
               "20k": 20.0, "half": 21.0975, "30k": 30.0, "marathon": 42.195}
TARGETS = ("5k", "10k", "half", "marathon")
GARMIN_KEYS = {"5k": "5k", "10k": "10k", "half": "media", "marathon": "maraton"}


# ---------------------------------------------------------------------------- fórmulas
def riegel(time_s: float, from_km: float, to_km: float, exponent: float | None = None) -> float:
    if exponent is None:
        exponent = rules.RIEGEL_MARATHON_EXPONENT if to_km > 40 and from_km < 21 else rules.RIEGEL_EXPONENT
    return time_s * (to_km / from_km) ** exponent


def _vo2(speed_m_min: float) -> float:
    return -4.60 + 0.182258 * speed_m_min + 0.000104 * speed_m_min ** 2


def _fraction(minutes: float) -> float:
    return 0.8 + 0.1894393 * math.exp(-0.012778 * minutes) + 0.2989558 * math.exp(-0.1932605 * minutes)


def vdot_from_race(km: float, time_s: float) -> float:
    """VDOT de Daniels-Gilbert para una marca."""
    minutes = time_s / 60
    return _vo2(km * 1000 / minutes) / _fraction(minutes)


def time_from_vdot(vdot: float, km: float) -> float:
    """Tiempo (s) en que un VDOT dado corre la distancia. El VDOT baja con el tiempo, así que se busca por
    bisección."""
    lo, hi = 1.0, 1000.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if vdot_from_race(km, mid * 60) > vdot:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2 * 60


def tanda_marathon(weekly_km: float, mean_pace_s: float) -> float:
    """Tiempo de maratón (s) según Tanda 2011: Pm = 17.1 + 140·e^(−0.0053·K) + 0.55·P (s/km)."""
    pace = 17.1 + 140.0 * math.exp(-0.0053 * weekly_km) + 0.55 * mean_pace_s
    return pace * DISTANCE_KM["marathon"]


def critical_speed(efforts: list[dict]) -> dict | None:
    """Regresión distancia = CS·tiempo + D′ con 3 o más esfuerzos distintos de 1,5 a 10 km."""
    points = sorted({(e["km"] * 1000, e["time_s"]) for e in efforts if 1.5 <= e["km"] <= 10})
    if len({d for d, _ in points}) < 3:
        return None
    n = len(points)
    mean_t = sum(t for _, t in points) / n
    mean_d = sum(d for d, _ in points) / n
    var = sum((t - mean_t) ** 2 for _, t in points)
    if not var:
        return None
    cs = sum((t - mean_t) * (d - mean_d) for d, t in points) / var
    d_prime = mean_d - cs * mean_t
    return {"cs_pace_s_per_km": round(1000 / cs, 1), "d_prime_m": round(d_prime)} if cs > 0 else None


def altitude_factor(altitude_m: float | None) -> float:
    """Cuánto más lento se corre a una altura respecto del nivel del mar (SUPUESTO, ver rules.py)."""
    if not altitude_m:
        return 1.0
    return 1 + rules.ALTITUDE_SLOWDOWN_PER_1000M * max(altitude_m - rules.ALTITUDE_FREE_M, 0) / 1000


# ---------------------------------------------------------------------------- combinación
def _weighted_quantile(values: list[tuple[float, float]], q: float) -> float:
    ordered = sorted(values)
    total = sum(w for _, w in ordered)
    acc = 0.0
    for value, weight in ordered:
        acc += weight
        if acc >= q * total:
            return value
    return ordered[-1][0]


def _recency_weight(effort_date: str | None, today: date | None) -> float:
    if not effort_date or not today:
        return 0.5 if today else 1.0
    age = (today - date.fromisoformat(effort_date)).days
    if age <= 90:
        return 1.0
    if age <= 180:
        return 0.7
    return 0.4 if age <= 365 else 0.0


def collect_efforts(profile_marks: dict[str, dict], records: dict | None) -> list[dict]:
    """Esfuerzos de referencia: marcas del perfil ({tiempo_s, fecha}) y récords de Strava (12 meses o de
    siempre). Si una distancia está en ambos, se usan los dos."""
    efforts = []
    for key, mark in profile_marks.items():
        if key in DISTANCE_KM and mark.get("tiempo_s"):
            efforts.append({"key": key, "km": DISTANCE_KM[key], "time_s": mark["tiempo_s"], "date": mark.get("fecha"),
                            "source": "perfil"})
    for key, r in ((records or {}).get("distances") or {}).items():
        best = r.get("last_12_months") or r.get("all_time")
        if key in DISTANCE_KM and best:
            efforts.append({"key": key, "km": DISTANCE_KM[key], "time_s": best["time_s"], "date": best.get("date"),
                            "source": "Strava"})
    return efforts


def training_indices(activities: list[dict], today: date, weeks: int = 8) -> dict | None:
    """Km por semana, ritmo medio y la carrera más larga de las últimas `weeks` semanas."""
    start = (today - timedelta(weeks=weeks)).isoformat()
    runs = [a for a in activities if a.get("sport", "running") == "running" and (a.get("date") or "") >= start
            and a.get("distance_km") and a.get("duration_s")]
    if not runs:
        return None
    km = sum(a["distance_km"] for a in runs)
    secs = sum(a["duration_s"] for a in runs)
    return {"weekly_km": round(km / weeks, 1), "mean_pace_s": round(secs / km, 1),
            "longest_km": max(a["distance_km"] for a in runs), "runs": len(runs)}


def predict(distance: str, efforts: list[dict], *, today: date | None = None, training: dict | None = None,
            garmin_s: float | None = None, home_altitude_m: float | None = None,
            race_altitude_m: float | None = None) -> dict:
    target_km = DISTANCE_KM[distance]
    estimates: list[dict] = []
    for e in efforts:
        recency = _recency_weight(e.get("date"), today)
        if not recency or e["km"] < 1.5:
            continue
        closeness = 1 / (1 + abs(math.log(target_km / e["km"])))
        base = f"{e['key']} {_hms(e['time_s'])} ({e['source']}{', ' + e['date'] if e.get('date') else ''})"
        r = riegel(e["time_s"], e["km"], target_km)
        r_weight = rules.PREDICTION_WEIGHTS["riegel"] * (0.7 if target_km > 40 and e["km"] < 21 else 1.0)
        estimates.append({"method": "Riegel", "from": base, "time_s": r, "weight": r_weight * recency * closeness})
        v = time_from_vdot(vdot_from_race(e["km"], e["time_s"]), target_km)
        v_weight = rules.PREDICTION_WEIGHTS["vdot"] * (0.5 if target_km > 40 and v > 4 * 3600 else 1.0)
        estimates.append({"method": "VDOT (Daniels)", "from": base, "time_s": v, "weight": v_weight * recency * closeness})
    if distance == "marathon" and training and training["runs"] >= 8:
        t = tanda_marathon(training["weekly_km"], training["mean_pace_s"])
        weight = rules.PREDICTION_WEIGHTS["tanda"] * (1.0 if 165 * 60 <= t <= 266 * 60 else 0.5)
        estimates.append({"method": "Tanda (entrenamiento)", "time_s": t, "weight": weight,
                          "from": f"{training['weekly_km']} km/semana a {_hms(training['mean_pace_s'])}/km"})
    if garmin_s:
        estimates.append({"method": "Garmin (VO2max)", "from": "reloj", "time_s": garmin_s,
                          "weight": rules.PREDICTION_WEIGHTS["garmin"][distance]})

    warnings = []
    if not estimates:
        return {"distancia": distance, "error": "No hay marcas recientes ni datos suficientes para predecir."}

    # Las marcas y el reloj reflejan la altura donde se entrena; se pasan a la altura de la carrera.
    factor = altitude_factor(race_altitude_m) / altitude_factor(home_altitude_m) \
        if race_altitude_m is not None and home_altitude_m is not None else 1.0
    for est in estimates:
        est["time_s"] *= factor

    pairs = [(est["time_s"], est["weight"]) for est in estimates if est["weight"] > 0]
    center = _weighted_quantile(pairs, 0.5)
    q_lo, q_hi = _weighted_quantile(pairs, 0.2), _weighted_quantile(pairs, 0.8)
    pct = rules.PREDICTION_RANGE_PCT[distance]
    low, high = min(center * (1 - pct), q_lo), max(center * (1 + pct), q_hi)
    spread = (q_hi - q_lo) / center
    freshest = min(((today - date.fromisoformat(e["date"])).days for e in efforts if e.get("date")), default=None) \
        if today else None
    methods = {est["method"] for est in estimates}

    if len(methods) == 1:
        warnings.append("Solo hay un método disponible: tómala con cautela.")
    if freshest is None or freshest > 180:
        warnings.append("No hay marcas de los últimos 6 meses: un test o una carrera corta la haría más precisa.")
    if training and distance in ("half", "marathon"):
        min_km, min_long = rules.DURABILITY_MIN[distance]
        if training["weekly_km"] < min_km or training["longest_km"] < min_long:
            warnings.append(f"Para {distance} conviene llegar con ≥{min_km} km/semana y un fondo de ≥{min_long} km "
                            f"(hoy {training['weekly_km']} km/semana, fondo más largo {training['longest_km']} km): "
                            "el tiempo real puede ser más lento que la predicción.")
    if distance in ("half", "marathon") and garmin_s:
        warnings.append("La predicción de Garmin suele ser optimista en distancias largas; pesa poco aquí.")

    if len(methods) == 1 or (freshest is not None and freshest > 270) or spread > 2 * pct:
        confidence = "baja"
    elif spread <= pct and freshest is not None and freshest <= 120 and len(methods) >= 2:
        confidence = "alta"
    else:
        confidence = "media"

    return {
        "distancia": distance,
        "tiempo": _hms(center), "tiempo_s": round(center),
        "rango": f"{_hms(low)} – {_hms(high)}", "rango_s": [round(low), round(high)],
        "ritmo": f"{_hms(center / target_km)}/km",
        "confianza": confidence,
        "ajuste_altura": (f"{(factor - 1) * 100:+.1f}% (entrenas a {home_altitude_m:.0f} m, carrera a "
                          f"{race_altitude_m:.0f} m)" if factor != 1.0 else None),
        "metodos": [{"metodo": est["method"], "desde": est["from"], "tiempo": _hms(est["time_s"]),
                     "peso": round(est["weight"], 2)} for est in sorted(estimates, key=lambda x: -x["weight"])][:8],
        "velocidad_critica": critical_speed([e for e in efforts if e.get("date") and today
                                             and (today - date.fromisoformat(e["date"])).days <= 120]),
        "advertencias": warnings,
    }


def _hms(seconds: float) -> str:
    seconds = round(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
