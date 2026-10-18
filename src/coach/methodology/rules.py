"""Reglas y tablas de la metodología "Corriendo conCiencia" (Andrés Ramírez).

Fuente: correr-conciencia.com (metodología, zonas, cumplimiento por colores, metrónomo,
planes de media maratón y el artículo "Cuántas veces hay que entrenar a la semana").

Todo lo marcado con `SUPUESTO` no está publicado por el método y se completa con
práctica estándar compatible (Joe Friel / TrainingPeaks). Revisar si se obtiene
información más precisa del entrenador.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------
# Test de umbral (publicado)
# --------------------------------------------------------------------------
THRESHOLD_TEST_MINUTES = 20
THRESHOLD_PACE_FACTOR = 1.05  # pace umbral = pace promedio del test × 1.05
THRESHOLD_HR_FACTOR = 0.95  # FC umbral = FC promedio del test × 0.95

# --------------------------------------------------------------------------
# Zonas Joe Friel for Running (el método indica usar este modelo en TrainingPeaks).
# Los porcentajes son los del modelo de Friel; verificar contra TrainingPeaks.
# --------------------------------------------------------------------------
# FC: % de la FC umbral (LTHR). (zona, mínimo, máximo)
FRIEL_HR_ZONES: list[tuple[str, float, float]] = [
    ("Z1", 0.00, 0.85),
    ("Z2", 0.85, 0.90),
    ("Z3", 0.90, 0.95),
    ("Z4", 0.95, 1.00),
    ("Z5a", 1.00, 1.03),
    ("Z5b", 1.03, 1.07),
    ("Z5c", 1.07, 1.30),
]
# Pace: % del pace umbral en seg/km (más alto = más lento). (zona, % más rápido, % más lento)
FRIEL_PACE_ZONES: list[tuple[str, float, float]] = [
    ("Z1", 1.29, 2.00),
    ("Z2", 1.14, 1.29),
    ("Z3", 1.06, 1.14),
    ("Z4", 1.00, 1.06),
    ("Z5a", 0.97, 1.00),
    ("Z5b", 0.90, 0.97),
    ("Z5c", 0.70, 0.90),
]
ZONE_ORDER = [z for z, _, _ in FRIEL_HR_ZONES]

# --------------------------------------------------------------------------
# Cumplimiento por colores (publicado; sobre TSS, luego duración y distancia)
# --------------------------------------------------------------------------
COMPLIANCE_GREEN = (0.80, 1.20)
COMPLIANCE_YELLOW_LOW = 0.50
COMPLIANCE_YELLOW_HIGH = 1.50

# --------------------------------------------------------------------------
# Cadencia y metrónomo (publicado)
# --------------------------------------------------------------------------
CADENCE_STEP_RPM = (2, 3)  # +2..3 rpm sobre la sesión equivalente anterior
CADENCE_REFERENCE_SPM = (155, 185)  # rango de referencia; 180 es un mito

# --------------------------------------------------------------------------
# Frecuencia semanal según marca actual (publicado)
# Cada columna: (sesiones_min, sesiones_max) -> marca en segundos para esa columna.
# --------------------------------------------------------------------------
_FREQ_COLUMNS = [(3, 3), (3, 4), (4, 4), (4, 5), (5, 5), (6, 6)]


def _t(h: int, m: int, s: int = 0) -> int:
    return h * 3600 + m * 60 + s


FREQUENCY_TABLE: dict[str, list[int]] = {
    "5k": [_t(0, 35), _t(0, 30), _t(0, 25), _t(0, 22), _t(0, 20), _t(0, 17)],
    "10k": [_t(1, 15), _t(1, 0), _t(0, 52), _t(0, 45), _t(0, 40), _t(0, 35)],
    "half": [_t(2, 45), _t(2, 20), _t(1, 55), _t(1, 40), _t(1, 30), _t(1, 20)],
    "marathon": [_t(5, 45), _t(5, 0), _t(4, 0), _t(3, 35), _t(3, 15), _t(2, 45)],
}

DISTANCE_KM = {"5k": 5.0, "10k": 10.0, "half": 21.0975, "marathon": 42.195}


def sessions_per_week(distance: str, best_time_s: int) -> tuple[int, int]:
    """Sesiones/semana (recomendado, máximo) para una marca. No entrenar más que el máximo."""
    thresholds = FREQUENCY_TABLE[distance]
    result = _FREQ_COLUMNS[0]
    for col, limit in zip(_FREQ_COLUMNS, thresholds):
        if best_time_s <= limit:
            result = col
    return result


# --------------------------------------------------------------------------
# Niveles de plan de media maratón (publicado). Duración: 24 semanas.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class PlanLevel:
    name: str
    category: str  # principiante / intermedio / avanzado
    sessions: int
    weekly_hours: float | None
    longest_session_min: int | None
    ten_k_range: tuple[int, int]  # segundos (min, max)
    half_range: tuple[int, int]


HALF_PLAN_WEEKS = 24
HALF_LEVELS: list[PlanLevel] = [
    PlanLevel("SUB 2h30", "principiante", 3, 2 + 40 / 60, 120, (_t(1, 11), _t(9, 0)), (_t(2, 26), _t(9, 0))),
    PlanLevel("SUB 2h15", "principiante", 3, 2 + 40 / 60, 120, (_t(1, 0), _t(1, 10, 59)), (_t(2, 11), _t(2, 25, 59))),
    PlanLevel("SUB 1h55", "intermedio", 4, 3 + 10 / 60, 130, (_t(0, 52), _t(0, 59, 59)), (_t(1, 51), _t(2, 10, 59))),
    PlanLevel("SUB 1h40", "intermedio", 5, 4 + 27 / 60, 120, (_t(0, 46), _t(0, 51, 59)), (_t(1, 36), _t(1, 50, 59))),
    PlanLevel("SUB 1h30", "avanzado", 5, 5 + 14 / 60, 130, (_t(0, 39), _t(0, 45, 59)), (_t(1, 26), _t(1, 35, 59))),
    PlanLevel("SUB 1h20", "avanzado", 6, None, None, (_t(0, 33), _t(0, 38, 59)), (_t(1, 15), _t(1, 25, 59))),
]


def half_level(ten_k_s: int | None = None, half_s: int | None = None) -> PlanLevel | None:
    """Nivel de plan de media maratón según la mejor marca de 10k o de media maratón."""
    for level in HALF_LEVELS:
        if half_s is not None and level.half_range[0] <= half_s <= level.half_range[1]:
            return level
        if ten_k_s is not None and level.ten_k_range[0] <= ten_k_s <= level.ten_k_range[1]:
            return level
    return None


def riegel(time_s: float, from_km: float, to_km: float, exponent: float = 1.06) -> float:
    """Equivalencia de marcas entre distancias (SUPUESTO: fórmula de Riegel, no es del método)."""
    return time_s * (to_km / from_km) ** exponent


# --------------------------------------------------------------------------
# Periodización
# --------------------------------------------------------------------------
PHASES = ["base", "build", "specialty", "taper"]
PHASE_LABELS = {
    "base": "Base",
    "build": "Construcción",
    "specialty": "Específico",
    "taper": "Afinamiento",
}
PHASE_PURPOSE = {
    "base": "Correr despacio y aumentar gradualmente la duración; fuerza muscular y tendones.",
    "build": "Aprender a dar pasos más largos y controlarlos con entrenamientos de velocidad.",
    "specialty": "Correr a ritmo de carrera de forma continua controlando la cadencia.",
    "taper": "Reducir volumen manteniendo intensidad para llegar fresco a la carrera.",
}
# SUPUESTO: proporción de cada fase (sin taper) y zonas permitidas.
PHASE_SPLIT = {"base": 0.40, "build": 0.35, "specialty": 0.25}
PHASE_ALLOWED_ZONES = {
    "base": {"Z1", "Z2"},
    "build": {"Z1", "Z2", "Z3", "Z4", "Z5a", "Z5b", "Z5c"},
    "specialty": {"Z1", "Z2", "Z3", "Z4", "Z5a", "Z5b"},
    "taper": {"Z1", "Z2", "Z3", "Z4", "Z5a"},
}
TAPER_WEEKS = {"5k": 1, "10k": 1, "half": 2, "marathon": 2}  # SUPUESTO
LOAD_WEEKS_PER_BLOCK = 3  # SUPUESTO: 3 semanas de carga + 1 de descarga
RECOVERY_WEEK_FACTOR = 0.70  # SUPUESTO: la semana de descarga baja ~30% el TSS
MAX_WEEKLY_TSS_INCREASE = 0.10  # SUPUESTO: no subir más de 10% el TSS semanal
MIN_BASE_WEEKS = 4  # SUPUESTO

# Intensidad típica (IF) por zona, para estimar TSS planeado. SUPUESTO (Friel).
ZONE_IF = {"Z1": 0.65, "Z2": 0.75, "Z3": 0.85, "Z4": 0.95, "Z5a": 1.0, "Z5b": 1.05, "Z5c": 1.12}

# --------------------------------------------------------------------------
# Primero lento: distribución de intensidad
# Fuentes: Andrés Ramírez ("¡Corre despacio!", ~80% en Z2, fondos en Z2, Z2 ≈ ritmo de 10K + 1 min/km);
# Esteve-Lanao et al. 2007 (JSCR 21(3):943-949); Oliveira et al. 2024 (Sports Med).
# --------------------------------------------------------------------------
EASY_ZONES = {"Z1", "Z2"}
MODERATE_ZONES = {"Z3"}
HARD_ZONES = {"Z4", "Z5a", "Z5b", "Z5c"}
EASY_SHARE_MIN = 0.80  # publicado: ~80% del tiempo en zona baja
# SUPUESTO: más exigente en base (solo correr despacio) y en afinamiento.
EASY_SHARE_MIN_BY_PHASE = {"base": 0.90, "build": EASY_SHARE_MIN, "specialty": EASY_SHARE_MIN, "taper": 0.85}
# SUPUESTO: sesiones de calidad (series, tempo, test o cualquier paso en Z3 o más) por semana.
MAX_QUALITY_SESSIONS = {"base": 1, "build": 2, "specialty": 2, "taper": 1}
MAX_QUALITY_SESSIONS_LOW_FREQ = 1  # quien entrena 3 días o menos
LONG_RUN_ZONES = {"Z1", "Z2"}  # publicado: los fondos casi siempre en Z2
LONG_RUN_FAST_SHARE_SPECIALTY = 0.25  # SUPUESTO: en el específico, hasta 25% del fondo a ritmo de carrera
EASY_PACE_OFFSET_FROM_10K_S = 60  # publicado: Z2 de ritmo ≈ 1 min/km más lenta que el ritmo de 10K

# SUPUESTO (Carlos Rojo, divulgación): sesiones de calidad bien preparadas y medidas.
QUALITY_WARMUP_MIN = 15  # calentamiento de 15-25 min a trote muy suave
MAX_INTERVAL_KM = {"5k": 6.0, "10k": 9.0, "half": 10.0, "marathon": 14.0}  # km de series por sesión

# SUPUESTO (convención de entrenadores, no umbral validado): desacople aeróbico en rodajes suaves.
DECOUPLING_OK = 0.05
DECOUPLING_MIN_MINUTES = 40

# SUPUESTO: ajuste de la semana por bienestar.
HRV_LOW_SD = 1.0  # HRV más de 1 desviación bajo su línea base...
HRV_LOW_DAYS = 2  # ...durante 2 días seguidos
SLEEP_LOW_HOURS = 6.0
SLEEP_LOW_DAYS = 3
READINESS_TSS_FACTOR = 0.85  # bajar 15% la carga
START_TSS_PER_SESSION = 35  # SUPUESTO: ~40 min en Z2 por sesión cuando no hay historial de carga


# --------------------------------------------------------------------------
# Predicción de carrera (ver analytics/prediction.py). Fórmulas publicadas: Riegel 1981, Daniels-Gilbert
# (VDOT), Tanda 2011. Pesos, rangos y ajustes son SUPUESTO basados en los errores reportados.
# --------------------------------------------------------------------------
RIEGEL_EXPONENT = 1.06
RIEGEL_MARATHON_EXPONENT = 1.08  # SUPUESTO: Riegel es optimista en maratón (Vickers & Vertosick 2016)
PREDICTION_WEIGHTS = {"riegel": 1.0, "vdot": 1.0, "tanda": 1.0,
                      "garmin": {"5k": 0.6, "10k": 0.6, "half": 0.4, "marathon": 0.3}}
PREDICTION_RANGE_PCT = {"5k": 0.02, "10k": 0.03, "half": 0.04, "marathon": 0.06}
# SUPUESTO (Peronnet 1991; Ciudad de México 2.240 m: 5K/10K ~4-6% más lentos): ~2,5% por cada 1.000 m
# por encima de 500 m.
ALTITUDE_SLOWDOWN_PER_1000M = 0.025
ALTITUDE_FREE_M = 500
# SUPUESTO: volumen mínimo (km/semana) y fondo más largo (km) para que la predicción larga sea realista.
DURABILITY_MIN = {"half": (25, 15), "marathon": (40, 28)}


def max_quality_sessions(phase: str, sessions_max: int) -> int:
    cap = MAX_QUALITY_SESSIONS[phase]
    return min(cap, MAX_QUALITY_SESSIONS_LOW_FREQ) if sessions_max <= 3 else cap
