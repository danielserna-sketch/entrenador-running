"""Tarjeta vertical (1080×1920) del último entrenamiento para publicar en redes.

Matplotlib sin pyplot: no depende del estado global de la figura y funciona en Lambda.
"""

from __future__ import annotations

import io
import math
import textwrap

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch

BG = "#05070a"
TEXT = "#f8fafc"
MUTED = "#8b93a7"
ACCENT = "#22d3ee"  # ruta y barra de zonas
BLUE = "#3b82f6"
ZONE_COLORS = {1: "#22d3ee", 2: "#3b82f6", 3: "#a78bfa", 4: "#f59e0b", 5: "#ef4444"}


def _route_xy(points: list[tuple[float, float]]) -> tuple[list[float], list[float]]:
    """Proyección plana local: la longitud se encoge con cos(latitud) para no deformar la ruta."""
    lat0 = sum(p[0] for p in points) / len(points)
    cos = math.cos(math.radians(lat0))
    return [p[1] * cos for p in points], [p[0] for p in points]


def render_workout_card(card: dict) -> bytes:
    """`card`: title, date_label, distance_km, pace, duration_min, avg_hr, zones [{zone, seconds}],
    route [(lat, lon)] (puede estar vacío), comment."""
    fig = Figure(figsize=(10.8, 19.2), dpi=100, facecolor=BG)
    FigureCanvasAgg(fig)

    fig.text(0.08, 0.93, card["date_label"].upper(), color=BLUE, fontsize=20, family="monospace", va="center")
    fig.text(0.08, 0.89, card["title"], color=TEXT, fontsize=72, weight="bold", va="center")

    # Mapa: solo si hay puntos GPS.
    ax = fig.add_axes([0.08, 0.56, 0.84, 0.28])
    ax.set_facecolor("#0b0f16")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color("#1f2937")
    route = card.get("route") or []
    if len(route) > 1:
        xs, ys = _route_xy(route)
        ax.plot(xs, ys, color=ACCENT, linewidth=7, solid_capstyle="round")
        ax.scatter([xs[-1]], [ys[-1]], s=260, color="#ffffff", zorder=3)
        ax.set_aspect("equal", adjustable="datalim")
    else:
        ax.text(0.5, 0.5, "Sin ruta GPS", color=MUTED, ha="center", va="center", fontsize=24,
                transform=ax.transAxes)

    metrics = [("DISTANCIA", f"{card['distance_km']:.1f}", "km", 0.08, 0.40),
               ("RITMO", card["pace"], "/km", 0.52, 0.40),
               ("TIEMPO", f"{card['duration_min']:.0f}", "min", 0.08, 0.29),
               ("FC MEDIA", f"{card['avg_hr']:.0f}" if card.get("avg_hr") else "—", "ppm", 0.52, 0.29)]
    renderer = fig.canvas.get_renderer()
    for label, value, unit, x, y in metrics:
        fig.text(x, y + 0.075, label, color=MUTED, fontsize=20, family="monospace")
        number = fig.text(x, y, value, color=TEXT, fontsize=96, weight="bold")
        # La unidad va justo después del número real, medido en píxeles.
        end = number.get_window_extent(renderer).x1 / fig.bbox.width
        fig.text(end + 0.012, y + 0.012, unit, color=MUTED, fontsize=30)

    # Barra de zonas: proporción de tiempo en cada zona de FC.
    zones = [z for z in (card.get("zones") or []) if z.get("seconds")]
    total = sum(z["seconds"] for z in zones)
    if total:
        bar = fig.add_axes([0.08, 0.235, 0.84, 0.018])
        bar.set_axis_off()
        left = 0.0
        for z in zones:
            width = z["seconds"] / total
            bar.barh(0, width, left=left, color=ZONE_COLORS.get(z["zone"], BLUE), height=1)
            left += width
        bar.set_xlim(0, 1)
        dominant = max(zones, key=lambda z: z["seconds"])
        fig.text(0.08, 0.27, f"Z{dominant['zone']} · ~{round(dominant['seconds'] / 60)} MIN",
                 color=MUTED, fontsize=18, family="monospace")

    # Comentario del coach.
    box = fig.add_axes([0.08, 0.075, 0.84, 0.16])
    box.set_axis_off()
    box.add_patch(FancyBboxPatch(
        (0, 0), 1, 1, boxstyle="round,pad=0.02,rounding_size=0.06", facecolor="#0b1220", edgecolor="#1f2937",
        transform=box.transAxes))
    box.text(0.06, 0.86, "TU COACH DICE", color=ACCENT, fontsize=18, family="monospace",
             transform=box.transAxes, va="top")
    box.text(0.06, 0.68, textwrap.fill(card["comment"], width=32), color=TEXT, fontsize=32,
             transform=box.transAxes, va="top", linespacing=1.3)

    fig.text(0.08, 0.035, "Running Agents.", color=TEXT, fontsize=38, weight="bold")
    fig.text(0.92, 0.04, "ENTRENADO CON IA", color=MUTED, fontsize=18, family="monospace", ha="right")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    return buf.getvalue()
