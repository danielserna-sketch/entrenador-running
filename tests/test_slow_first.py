"""Primero lento: distribución 80/20, calidad limitada, fondos en Z2, ritmo fácil máximo, semana segura,
ajuste por bienestar, cumplimiento de intensidad y desacople aeróbico."""

from datetime import date, timedelta

from coach.analytics import compliance, decoupling
from coach.analytics.zones import easy_pace_ceiling
from coach.methodology import rules
from coach.planning import microcycle, readiness, validator
from coach.planning.assessment import assess
from coach.planning.garmin_workout_builder import build_workout

MONDAY = date(2026, 10, 19)
ALL_DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def day(i):
    return (MONDAY + timedelta(days=i)).isoformat()


def session(i, kind="easy", steps=None, title=None):
    return {"date": day(i), "title": title or f"{kind} {i}", "type": kind, "description": "",
            "cadence_focus": False, "steps": steps or [{"kind": "main", "duration_min": 45, "zone": "Z2"}]}


def intervals(i, main_min=20, zone="Z4", warmup=15):
    steps = [{"kind": "main", "duration_min": main_min, "zone": zone}, {"kind": "cooldown", "duration_min": 10, "zone": "Z1"}]
    if warmup:
        steps.insert(0, {"kind": "warmup", "duration_min": warmup, "zone": "Z1"})
    return session(i, "intervals", steps)


def validate(sessions, zones, phase="build", **kw):
    week = {"sessions": sessions}
    target = sum(validator.session_tss(s, zones) for s in sessions)
    return validator.validate_week(week, week_start=MONDAY, phase=phase, target_tss=target, sessions_max=5,
                                   days_available=ALL_DAYS, longest_session_min=150, needs_test=False,
                                   zones=zones, **kw)


# ---------------------------------------------------------------------------- ritmo fácil máximo
def test_easy_ceiling_takes_the_slower_pace(zones):
    z2_fast = next(z for z in zones["pace"] if z["zone"] == "Z2")["fast_s"]
    assert easy_pace_ceiling(zones, 48 * 60) == 48 * 6 + 60  # 10K 48:00 → 5:48, más lento que la Z2 de Friel
    assert easy_pace_ceiling(zones, 40 * 60) == z2_fast  # 10K 40:00 → 5:00, la Z2 de Friel es más lenta
    assert easy_pace_ceiling(zones, None) == z2_fast


def test_garmin_easy_target_respects_ceiling(zones):
    w = build_workout(session(1), {**zones, "easy_ceiling_s": 348}, prefer="pace")
    step = w["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetValueTwo"] == round(1000 / 348, 3)  # límite rápido = 5:48/km
    assert step["targetValueOne"] < step["targetValueTwo"]
    # Por defecto el rodaje va por FC y el techo de ritmo queda en las notas.
    w = build_workout(session(1), {**zones, "easy_ceiling_s": 348})
    assert w["workoutSegments"][0]["workoutSteps"][0]["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert "no más rápido que 5:48/km" in w["description"]


# ---------------------------------------------------------------------------- validador
def test_week_with_too_little_easy_time_fails(zones):
    errors = validate([intervals(1, main_min=40), session(3, steps=[{"kind": "main", "duration_min": 30, "zone": "Z2"}])],
                      zones)
    assert any("del tiempo es en Z1-Z2" in e for e in errors)


def test_good_build_week_passes(zones):
    errors = validate([intervals(1), session(3), session(5, "long", [{"kind": "main", "duration_min": 120, "zone": "Z2"}])],
                      zones)
    assert errors == []


def test_long_run_must_be_easy_except_some_race_pace_in_specialty(zones):
    long_steps = [{"kind": "main", "duration_min": 90, "zone": "Z2"}, {"kind": "main", "duration_min": 20, "zone": "Z3"}]
    base_week = [session(1), session(3), session(5, "long", long_steps)]
    assert any("debe ir en Z1-Z2" in e for e in validate(base_week, zones))
    assert not any("fondo" in e for e in validate(base_week, zones, phase="specialty"))


def test_too_many_quality_sessions_fail_and_z3_counts(zones):
    tempo = session(3, "easy", [{"kind": "main", "duration_min": 30, "zone": "Z3"}], title="Rodaje alegre")
    week = [intervals(1), tempo, intervals(5), session(6, "long", [{"kind": "main", "duration_min": 150, "zone": "Z2"}])]
    assert any("sesiones de calidad" in e for e in validate(week, zones))
    assert validator.is_quality(tempo)


def test_quality_session_needs_warmup_and_interval_volume_cap(zones):
    long = session(6, "long", [{"kind": "main", "duration_min": 150, "zone": "Z2"}])
    no_warmup = validate([intervals(1, warmup=0), session(3), long], zones)
    assert any("calentamiento" in e for e in no_warmup)
    big = session(1, "tempo", [{"kind": "warmup", "duration_min": 15, "zone": "Z1"},
                               {"kind": "interval", "distance_km": 10, "zone": "Z3"}])
    assert any("km de series" in e for e in validate([big, session(3), long], zones, distance="10k"))


# ---------------------------------------------------------------------------- semana segura
def build_ctx(**over):
    ctx = {"week": {"index": 6, "week_start": MONDAY.isoformat(), "phase": "build", "phase_label": "Construcción",
                    "kind": "carga", "target_tss": 220, "target_hours": 4},
           "distance": "half", "sessions_recommended": 4, "sessions_max": 4, "max_quality": 2,
           "days_available": ["martes", "jueves", "sábado", "domingo"], "longest_session_min": 130,
           "needs_test": False, "race": {"date": "2027-04-04", "distance": "half"}}
    return {**ctx, **over}


def test_fallback_week_is_valid(zones):
    ctx = build_ctx()
    week = microcycle.fallback_week(ctx, zones)
    errors = validator.validate_week(week, week_start=MONDAY, phase="build", target_tss=220, sessions_max=4,
                                     days_available=ctx["days_available"], longest_session_min=130,
                                     needs_test=False, zones=zones, distance="half", max_quality=2)
    assert errors == []
    assert {s["type"] for s in week["sessions"]} == {"easy", "long"}


class AlwaysWrongLLM:
    def complete_json(self, system, prompt, schema, tier="main"):
        return {"summary": "x", "strength_notes": "",
                "sessions": [{"date": day(1), "title": "Series", "type": "intervals", "description": "",
                              "cadence_focus": False, "steps": [{"kind": "main", "duration_min": 60, "zone": "Z5b"}]}]}


def test_generate_week_uses_fallback_when_llm_keeps_failing(zones):
    week = microcycle.generate_week(AlwaysWrongLLM(), None, build_ctx(), zones, [])
    assert week["fallback"] is True and week["validation_errors"] == []
    assert week["llm_errors"]


# ---------------------------------------------------------------------------- bienestar y carga inicial
def dailies(hrv, sleep):
    start = date(2026, 9, 18)
    return [{"date": (start + timedelta(days=i)).isoformat(), "hrv_last_night": h, "sleep_hours": s}
            for i, (h, s) in enumerate(zip(hrv, sleep))]


def test_readiness_lowers_load_on_low_hrv_bad_sleep_or_pain():
    today = date(2026, 10, 18)
    normal = dailies([60 + (i % 3) for i in range(31)], [7.5] * 31)
    assert readiness.assess(normal, [], today)["tss_factor"] == 1.0
    low_hrv = dailies([60 + (i % 3) for i in range(29)] + [45, 44], [7.5] * 31)
    out = readiness.assess(low_hrv, [], today)
    assert out["tss_factor"] == rules.READINESS_TSS_FACTOR and out["quality_delta"] == -1 and "HRV" in out["reason"]
    short_sleep = dailies([60 + (i % 3) for i in range(31)], [7.5] * 28 + [5, 5.5, 5.8])
    assert "sueño" in readiness.assess(short_sleep, [], today)["reason"]
    pain = [{"date": "2026-10-16", "pain": 5, "pain_location": "rodilla"}]
    assert "rodilla" in readiness.assess(normal, pain, today)["reason"]


def test_start_load_without_history_scales_with_sessions():
    diag = assess({"goal_distance": "10k", "days_available": ["martes", "jueves", "domingo"]}, [], None,
                  date(2026, 10, 18))
    assert diag["start_weekly_tss"] == diag["sessions_recommended"] * rules.START_TSS_PER_SESSION


# ---------------------------------------------------------------------------- cumplimiento de intensidad
def test_easy_run_done_too_fast_is_not_green(zones):
    planned = {"date": "2026-10-20", "type": "easy", "main_zone": "Z2", "tss": 40}
    run = {"date": "2026-10-20", "tss": 42}
    assert compliance.session_compliance(planned, {**run, "avg_hr": 140}, zones=zones)["color"] == compliance.GREEN
    assert compliance.session_compliance(planned, {**run, "avg_hr": 148}, zones=zones)["color"] == compliance.YELLOW
    result = compliance.session_compliance(planned, {**run, "avg_hr": 158}, zones=zones)
    assert result["color"] == compliance.ORANGE and result["volume_color"] == compliance.GREEN
    series = {**planned, "type": "intervals"}
    assert compliance.session_compliance(series, {**run, "avg_hr": 158}, zones=zones)["color"] == compliance.GREEN


# ---------------------------------------------------------------------------- desacople aeróbico
def test_aerobic_decoupling():
    steady = [{"duration_s": 600, "pace_s_per_km": 360, "avg_hr": 140}] * 6
    assert decoupling.aerobic_decoupling(steady) == 0.0
    drifting = steady[:3] + [{"duration_s": 600, "pace_s_per_km": 360, "avg_hr": 147}] * 3
    assert abs(decoupling.aerobic_decoupling(drifting) - 0.048) < 0.002
    assert decoupling.aerobic_decoupling(steady[:3]) is None  # muy corto


# ---------------------------------------------------------------------------- no publicar semanas inválidas
class CountingGarmin:
    def __init__(self):
        self.created = []

    def create_workout(self, workout):
        self.created.append(workout)
        return "1"

    def schedule_workout(self, workout_id, day):
        pass

    def delete_workout(self, workout_id):
        pass


def test_invalid_week_is_not_published_and_old_week_is_kept(repo, zones, monkeypatch):
    from coach.planning.service import PlanService

    garmin = CountingGarmin()
    service = PlanService(repo, None, None, lambda: garmin, date(2026, 10, 18))
    old = {"week_start": MONDAY.isoformat(), "index": 6, "sessions": [session(1)], "validation_errors": []}
    repo.save_week(old)
    monkeypatch.setattr(service, "week_context", lambda monday, adjustments=None: build_ctx())
    monkeypatch.setattr(microcycle, "generate_week",
                        lambda *a, **k: {**build_ctx()["week"], "sessions": [], "validation_errors": ["mal"]})

    result = service.generate_week(MONDAY)
    assert result["needs_review"] is True and result["rejected_errors"] == ["mal"]
    assert garmin.created == []
    assert repo.get_week(MONDAY)["sessions"] == old["sessions"]
