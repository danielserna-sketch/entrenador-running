"""Gráficas PNG para el correo (Gmail/Outlook no muestran SVG ni JavaScript)."""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

INK = "#1f2933"
MUTED = "#8a94a6"
PLANNED = "#c7d2fe"
ACTUAL = "#4f46e5"
CTL = "#2563eb"
ATL = "#f97316"
TSB = "#10b981"


def _finish(fig) -> bytes:
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=144)
    plt.close(fig)
    return buf.getvalue()


def _style(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(MUTED)
    ax.spines["bottom"].set_color(MUTED)
    ax.tick_params(colors=INK, labelsize=8)
    ax.grid(axis="y", color="#e5e7eb", linewidth=0.6)
    ax.set_axisbelow(True)


def tss_planned_vs_actual(weeks: list[dict]) -> bytes:
    """weeks: [{"label": "S1", "planned": 200, "actual": 180|None}]"""
    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    x = range(len(weeks))
    ax.bar(x, [w["planned"] for w in weeks], color=PLANNED, width=0.75, label="Planeado")
    ax.bar(x, [w["actual"] or 0 for w in weeks], color=ACTUAL, width=0.45, label="Realizado")
    ax.set_xticks(list(x), [w["label"] for w in weeks], fontsize=7)
    ax.set_ylabel("TSS semanal", fontsize=8, color=INK)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    _style(ax)
    return _finish(fig)


def fitness(series: list[dict]) -> bytes:
    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    dates = [s["date"][5:] for s in series]
    ax.plot(dates, [s["ctl"] for s in series], color=CTL, linewidth=2, label="Forma (CTL)")
    ax.plot(dates, [s["atl"] for s in series], color=ATL, linewidth=1.2, label="Fatiga (ATL)")
    ax.plot(dates, [s["tsb"] for s in series], color=TSB, linewidth=1.2, linestyle="--", label="Frescura (TSB)")
    step = max(1, len(dates) // 8)
    ax.set_xticks(dates[::step])
    ax.legend(frameon=False, fontsize=8, loc="upper left", ncol=3)
    _style(ax)
    return _finish(fig)


def cadence(trend: list[dict]) -> bytes:
    fig, ax = plt.subplots(figsize=(7.2, 2.2))
    ax.plot([t["date"][5:] for t in trend], [t["rpm"] for t in trend], color=ACTUAL, marker="o",
            markersize=3, linewidth=1.5)
    ax.set_ylabel("Cadencia (rpm)", fontsize=8, color=INK)
    step = max(1, len(trend) // 8)
    ax.set_xticks([t["date"][5:] for t in trend][::step])
    _style(ax)
    return _finish(fig)
