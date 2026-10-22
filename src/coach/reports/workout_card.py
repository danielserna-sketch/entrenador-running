"""Tarjeta vertical (1080×1920) del último entrenamiento para publicar en redes.

Réplica de la referencia de diseño (línea gráfica de Agents by Stratika): Plus Jakarta Sans 800 con tracking
negativo para titulares y cifras, IBM Plex Mono espaciada para rótulos. Todo se dibuja en un único eje que cubre
la figura con 1 unidad = 1 píxel y el origen arriba a la izquierda, así las medidas se copian tal cual.
Matplotlib sin pyplot: no depende del estado global y funciona en Lambda.
"""

from __future__ import annotations

import io
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Circle, FancyBboxPatch

W, H = 1080, 1920
FONTS = Path(__file__).parent / "fonts"

BG = "#060810"
TEXT = "#EEF2FC"
MUTED = "#9AAAC4"
UNIT = "#8C96AA"
CYAN = "#22D3EE"
BLUE = "#3B82F6"
PANEL = "#090D17"
BORDER = "#1A2233"
ZONE_COLORS = {1: CYAN, 2: BLUE, 3: "#7DD3FC", 4: "#F59E0B", 5: "#EF4444"}

X0, X1 = 80, 998  # márgenes de contenido
PX_TO_PT = 72 / 100  # dpi 100


@lru_cache
def _font(name: str) -> str:
    return str(FONTS / name)


def _fp(name: str, size_px: float) -> FontProperties:
    return FontProperties(fname=_font(name), size=size_px * PX_TO_PT)


SANS_800 = "PlusJakartaSans-ExtraBold.ttf"
SANS_500 = "PlusJakartaSans-Medium.ttf"
MONO = "IBMPlexMono-Regular.ttf"
MONO_500 = "IBMPlexMono-Medium.ttf"


class _Canvas:
    def __init__(self):
        self.fig = Figure(figsize=(W / 100, H / 100), dpi=100, facecolor=BG)
        FigureCanvasAgg(self.fig)
        self.renderer = self.fig.canvas.get_renderer()
        ax = self.fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, W)
        ax.set_ylim(H, 0)
        ax.set_axis_off()
        self.ax = ax

    def width(self, s: str, fp: FontProperties) -> float:
        if not s:
            return 0.0
        w, _, _ = self.renderer.get_text_width_height_descent(s, fp, ismath=False)
        return w

    def text(self, x: float, y: float, s: str, font: str, size_px: float, color: str, *,
             tracking: float = 0.0, ha: str = "left", va: str = "baseline") -> float:
        """Texto con tracking en píxeles (negativo para titulares). Devuelve la x donde termina."""
        fp = _fp(font, size_px)
        total = self.width(s, fp) + tracking * max(len(s) - 1, 0)
        start = x - total if ha == "right" else x
        if not tracking:
            self.ax.text(start, y, s, fontproperties=fp, color=color, va=va, ha="left")
            return start + total
        for i, ch in enumerate(s):
            # La posición de cada letra sale del ancho del prefijo: respeta el kerning de la fuente.
            cx = start + self.width(s[:i], fp) + tracking * i
            self.ax.text(cx, y, ch, fontproperties=fp, color=color, va=va, ha="left")
        return start + total

    def rounded(self, x, y, w, h, radius, face, edge=None, lw=0.0, z=1):
        patch = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
                               facecolor=face, edgecolor=edge or face, linewidth=lw, zorder=z)
        self.ax.add_patch(patch)
        return patch

    def png(self) -> bytes:
        buf = io.BytesIO()
        self.fig.savefig(buf, format="png", facecolor=BG)
        return buf.getvalue()


def _wrap(c: _Canvas, text: str, fp: FontProperties, max_w: float, max_lines: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if c.width(candidate, fp) <= max_w or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,;") + "…"
    return lines


def _glow(c: _Canvas, x, y, w, h, clip):
    """Halo azul muy tenue en el centro del panel del mapa."""
    yy, xx = np.mgrid[-1:1:200j, -1:1:200j]
    alpha = np.clip(1 - np.sqrt(xx**2 + (yy * 1.15) ** 2), 0, 1) ** 1.6 * 0.85
    rgba = np.zeros((200, 200, 4))
    rgba[..., :3] = (0.08, 0.17, 0.33)
    rgba[..., 3] = alpha
    img = c.ax.imshow(rgba, extent=(x, x + w, y + h, y), origin="upper", zorder=2, interpolation="bilinear")
    img.set_clip_path(clip)


def _route(c: _Canvas, points: list[tuple[float, float]], x, y, w, h, pad=70):
    lat0 = sum(p[0] for p in points) / len(points)
    cos = math.cos(math.radians(lat0))
    xs = [p[1] * cos for p in points]
    ys = [p[0] for p in points]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1e-9
    scale = min((w - 2 * pad) / ((max(xs) - min(xs)) or span), (h - 2 * pad) / ((max(ys) - min(ys)) or span))
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    px = [x + w / 2 + (v - cx) * scale for v in xs]
    py = [y + h / 2 - (v - cy) * scale for v in ys]  # norte arriba
    for lw, alpha in ((34, 0.06), (20, 0.12), (12, 0.22)):
        c.ax.plot(px, py, color=CYAN, linewidth=lw * PX_TO_PT, alpha=alpha, solid_capstyle="round",
                  solid_joinstyle="round", zorder=3)
    c.ax.plot(px, py, color=CYAN, linewidth=7 * PX_TO_PT, solid_capstyle="round", solid_joinstyle="round", zorder=4)
    c.ax.add_patch(Circle((px[-1], py[-1]), 22, color=CYAN, alpha=0.18, zorder=5))
    c.ax.add_patch(Circle((px[-1], py[-1]), 11, facecolor="#FFFFFF", edgecolor=CYAN, linewidth=4 * PX_TO_PT, zorder=6))


def _runner_icon(c: _Canvas, cx: float, cy: float, r: float):
    """Círculo azul con un corredor de trazo blanco (estilo icono outline)."""
    c.ax.add_patch(Circle((cx, cy), r, color=BLUE, zorder=3))
    s = r / 32
    lw = 3.2 * s * PX_TO_PT
    kw = {"color": "#FFFFFF", "linewidth": lw, "solid_capstyle": "round", "solid_joinstyle": "round", "zorder": 4}
    c.ax.add_patch(Circle((cx + 4 * s, cy - 15 * s), 4.2 * s, fill=False, edgecolor="#FFFFFF", linewidth=lw, zorder=4))
    c.ax.plot([cx + 2 * s, cx - 3 * s, cx - 9 * s, cx - 14 * s], [cy - 8 * s, cy + 4 * s, cy + 9 * s, cy + 16 * s], **kw)
    c.ax.plot([cx - 3 * s, cx + 4 * s, cx + 6 * s], [cy + 4 * s, cy + 9 * s, cy + 17 * s], **kw)
    c.ax.plot([cx - 9 * s, cx - 2 * s, cx + 6 * s, cx + 11 * s], [cy - 3 * s, cy - 9 * s, cy - 6 * s, cy - 1 * s], **kw)


def render_workout_card(card: dict) -> bytes:
    """`card`: title, date_label, distance_km, pace, duration_min, avg_hr, zones [{zone, seconds}],
    route [(lat, lon)] (puede estar vacío), comment."""
    c = _Canvas()

    # Fecha con raya azul y titular.
    c.ax.plot([X0, 135], [168, 168], color=BLUE, linewidth=4 * PX_TO_PT, solid_capstyle="butt")
    c.text(154, 168, card["date_label"].upper(), MONO_500, 25, BLUE, tracking=5.5, va="center")
    title_size = 100
    while c.width(card["title"], _fp(SANS_800, title_size)) - 4 * len(card["title"]) > X1 - X0 and title_size > 60:
        title_size -= 4
    c.text(X0, 290, card["title"], SANS_800, title_size, TEXT, tracking=-title_size * 0.04)

    # Panel del mapa.
    mx, my, mw, mh = 82, 357, 916, 520
    panel = c.rounded(mx, my, mw, mh, 38, PANEL, BORDER, lw=1.5, z=1)
    _glow(c, mx, my, mw, mh, panel)
    route = card.get("route") or []
    if len(route) > 1:
        _route(c, route, mx, my, mw, mh)
    else:
        c.text(mx + mw / 2 - c.width("SIN RUTA GPS", _fp(MONO, 22)) / 2 - 24, my + mh / 2, "SIN RUTA GPS", MONO, 22,
               MUTED, tracking=4, va="center")

    # Métricas: rótulo mono espaciado, cifra 800 con tracking negativo y unidad gris pegada.
    cols = (X0, 564)
    rows = ((943, 1044), (1116, 1219))
    metrics = [("DISTANCIA", f"{card['distance_km']:.1f}", "km"), ("RITMO", card["pace"], "/km"),
               ("TIEMPO", f"{card['duration_min']:.0f}", "min"),
               ("FC MEDIA", f"{card['avg_hr']:.0f}" if card.get("avg_hr") else "—", "ppm")]
    for i, (label, value, unit) in enumerate(metrics):
        x = cols[i % 2]
        label_y, value_y = rows[i // 2]
        c.text(x, label_y, label, MONO, 24, MUTED, tracking=4.2, va="center")
        end = c.text(x, value_y, value, SANS_800, 88, TEXT, tracking=-4.5)
        c.text(end + 3, value_y, unit, SANS_800, 44, UNIT, tracking=-1.8)

    # Zonas de FC: primera y última con tiempo, y barra redondeada con separación entre tramos.
    zones = sorted((z for z in (card.get("zones") or []) if z.get("seconds")), key=lambda z: z["zone"])
    total = sum(z["seconds"] for z in zones)
    if total:
        def label(z):
            return f"Z{z['zone']} · ~{round(z['seconds'] / 60)} MIN"

        c.text(X0, 1299, label(zones[0]), MONO, 24, MUTED, tracking=3.8, va="center")
        if len(zones) > 1:
            c.text(X1, 1299, label(zones[-1]), MONO, 24, MUTED, tracking=3.8, ha="right", va="center")
        gap, bar_h, left = 6, 23, float(X0)
        usable = (X1 - X0) - gap * (len(zones) - 1)
        for z in zones:
            seg = max(usable * z["seconds"] / total, bar_h)
            c.rounded(left, 1331, seg, bar_h, bar_h / 2, ZONE_COLORS.get(z["zone"], BLUE), z=2)
            left += seg + gap

    # Caja "Tu coach dice".
    bx, by, bw, bh = 82, 1402, 916, 194
    c.rounded(bx, by, bw, bh, 30, "#0B111C", "#1E293B", lw=1.5, z=1)
    _runner_icon(c, 148, 1467, 32)
    c.text(204, 1450, "TU COACH DICE", MONO_500, 24, CYAN, tracking=3, va="center")
    fp = _fp(SANS_500, 36.5)
    for i, line in enumerate(_wrap(c, card["comment"], fp, bx + bw - 204 - 36, 2)):
        c.text(204, 1506 + i * 47, line, SANS_500, 36.5, TEXT)

    # Pie: logo con punto cian y rótulo.
    end = c.text(X0, 1768, "Running Agents", SANS_800, 50, TEXT, tracking=-2.4)
    c.text(end - 1, 1768, ".", SANS_800, 50, CYAN)
    c.text(X1, 1758, "ENTRENADO CON IA", MONO, 21, MUTED, tracking=4, ha="right", va="center")

    return c.png()
