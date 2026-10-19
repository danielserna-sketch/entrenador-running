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
ACCENT = "#22d3ee"  # ruta, brillo y barra de zonas
BLUE = "#3b82f6"
ZONE_COLORS = {1: "#22d3ee", 2: "#3b82f6", 3: "#a78bfa", 4: "#f59e0b", 5: "#ef4444"}


def _route_xy(points: list[tuple[float, float]]) -> tuple[list[float], list[float]]:
    """Proyección plana local: la longitud se encoge con cos(latitud) para no deformar la ruta."""
    lat0 = sum(p[0] for p in points) / len(points)
    cos = math.cos(math.radians(lat0))
    return [p[1] * cos for p in points], [p[0] for p in points]


def _zone_label(zone: dict) -> str:
    return f"Z{zone['zone']} · ~{round(zone['seconds'] / 60)} MIN"


def render_workout_card(card: dict) -> bytes:
    """`card`: title, date_label, distance_km, pace, duration_min, avg_hr, zones [{zone, seconds}],
    route [(lat, lon)] (puede estar vacío), comment."""
    fig = Figure(figsize=(10.8, 19.2), dpi=100, facecolor=BG)
    FigureCanvasAgg(fig)
    renderer = fig.canvas.get_renderer()

    fig.text(0.08, 0.955, card["date_label"].upper(), color=BLUE, fontsize=20, family="monospace", va="center")
    # Títulos largos (nombre de la actividad) se parten en dos líneas y bajan de tamaño.
    lines = textwrap.wrap(card["title"], width=16) or [card["title"]]
    size = 72 if len(lines) == 1 else 56
    fig.text(0.08, 0.90, "\n".join(lines[:2]), color=TEXT, fontsize=size, weight="bold", va="center",
             linespacing=1.0)

    # Mapa: solo si hay puntos GPS.
    ax = fig.add_axes([0.08, 0.545, 0.84, 0.27])
    ax.set_facecolor("#0b0f16")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color("#1f2937")
    route = card.get("route") or []
    if len(route) > 1:
        xs, ys = _route_xy(route)
        ax.plot(xs, ys, color=ACCENT, linewidth=28, alpha=0.12, solid_capstyle="round")  # brillo
        ax.plot(xs, ys, color=ACCENT, linewidth=7, solid_capstyle="round")
        ax.scatter([xs[-1]], [ys[-1]], s=260, color="#ffffff", zorder=3)
        ax.set_aspect("equal", adjustable="datalim")
    else:
        ax.text(0.5, 0.5, "Sin ruta GPS", color=MUTED, ha="center", va="center", fontsize=24,
                transform=ax.transAxes)

    rows = {"top": 0.42, "bottom": 0.31}
    metrics = [("DISTANCIA", f"{card['distance_km']:.1f}", "km", 0.08, rows["top"]),
               ("RITMO", card["pace"], "/km", 0.52, rows["top"]),
               ("TIEMPO", f"{card['duration_min']:.0f}", "min", 0.08, rows["bottom"]),
               ("FC MEDIA", f"{card['avg_hr']:.0f}" if card.get("avg_hr") else "—", "ppm", 0.52, rows["bottom"])]
    for label, value, unit, x, y in metrics:
        fig.text(x, y + 0.075, label, color=MUTED, fontsize=20, family="monospace")
        number = fig.text(x, y, value, color=TEXT, fontsize=96, weight="bold")
        # La unidad va justo después del número real, medido en píxeles.
        end = number.get_window_extent(renderer).x1 / fig.bbox.width
        fig.text(end + 0.012, y + 0.012, unit, color=MUTED, fontsize=30)

    # Zonas de FC: barra proporcional y, arriba, la primera y la última zona con tiempo.
    zones = sorted((z for z in (card.get("zones") or []) if z.get("seconds")), key=lambda z: z["zone"])
    total = sum(z["seconds"] for z in zones)
    if total:
        bar = fig.add_axes([0.08, 0.245, 0.84, 0.018])
        bar.set_axis_off()
        left = 0.0
        for z in zones:
            width = z["seconds"] / total
            bar.barh(0, width, left=left, color=ZONE_COLORS.get(z["zone"], BLUE), height=1)
            left += width
        bar.set_xlim(0, 1)
        fig.text(0.08, 0.275, _zone_label(zones[0]), color=MUTED, fontsize=18, family="monospace")
        if len(zones) > 1:
            fig.text(0.92, 0.275, _zone_label(zones[-1]), color=MUTED, fontsize=18, family="monospace", ha="right")

    # Comentario del coach.
    box = fig.add_axes([0.08, 0.085, 0.84, 0.15])
    box.set_axis_off()
    box.add_patch(FancyBboxPatch(
        (0, 0), 1, 1, boxstyle="round,pad=0.02,rounding_size=0.06", facecolor="#0b1220", edgecolor="#1f2937",
        transform=box.transAxes))
    box.text(0.06, 0.86, "TU COACH DICE", color=ACCENT, fontsize=18, family="monospace",
             transform=box.transAxes, va="top")
    box.text(0.06, 0.66, textwrap.fill(card["comment"], width=32), color=TEXT, fontsize=32,
             transform=box.transAxes, va="top", linespacing=1.3)

    # Logo: "Running Agents" con el punto cian.
    brand = fig.text(0.08, 0.035, "Running Agents", color=TEXT, fontsize=38, weight="bold")
    end = brand.get_window_extent(renderer).x1 / fig.bbox.width
    fig.text(end, 0.035, ".", color=ACCENT, fontsize=38, weight="bold")
    fig.text(0.92, 0.04, "ENTRENADO CON IA", color=MUTED, fontsize=18, family="monospace", ha="right")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    return buf.getvalue()
