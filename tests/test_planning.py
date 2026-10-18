import itertools
from datetime import date

from coach.planning import assessment, periodization, validator
from coach.planning.garmin_workout_builder import build_workout


def test_half_plan_24_weeks_structure():
    macro = periodization.build_macrocycle(race_date=date(2027, 4, 4), start=date(2026, 10, 19),
                                           distance="half", start_weekly_tss=180, weekly_hours_target=4.45)
    weeks = macro["weeks"]
    assert macro["total_weeks"] == 24 and weeks[0]["week_start"] == "2026-10-19"
    phases = [w["phase"] for w in weeks]
    assert phases[0] == "base" and phases[-1] == "taper"
    assert phases == sorted(phases, key=["base", "build", "specialty", "taper"].index)
    assert weeks[3]["kind"] == "descarga" and weeks[3]["target_tss"] < weeks[2]["target_tss"]
    assert weeks[-1]["kind"] == "carrera"
    loads = [w["target_tss"] for w in weeks if w["kind"] == "carga"]
    for a, b in itertools.pairwise(loads):
        assert b <= a * 1.10 + 1
    assert macro["warnings"] == []


def test_short_plan_warns():
    macro = periodization.build_macrocycle(race_date=date(2027, 1, 31), start=date(2026, 10, 19),
                                           distance="half", start_weekly_tss=150)
    assert macro["warnings"]


def test_plan_starts_monday():
    assert periodization.plan_start(date(2026, 10, 17)) == date(2026, 10, 19)
    assert periodization.plan_start(date(2026, 10, 19)) == date(2026, 10, 19)


def test_assessment(history):
    profile = {"goal_distance": "half", "best_times": {"10k": 48 * 60},
               "days_available": ["martes", "jueves", "sábado", "domingo"]}
    diag = assessment.assess(profile, history, None, date(2026, 10, 17))
    assert diag["half_level"]["name"] == "SUB 1h40"
    assert diag["sessions_max"] == 4  # tabla permite 5, pero solo hay 4 días
    assert diag["needs_threshold_test"] is True


def _session(day, kind="easy", zone="Z2", minutes=45, title=None):
    return {"date": day, "title": title or f"{kind} {day}", "type": kind, "description": "", "cadence_focus": False,
            "steps": [{"kind": "main", "duration_min": minutes, "zone": zone}]}


def test_validator_catches_rule_breaks(zones):
    week = {"sessions": [
        _session("2026-10-20", "intervals", "Z5b"),
        _session("2026-10-21", "tempo", "Z4"),
        _session("2026-10-27", minutes=200),
    ]}
    errors = validator.validate_week(week, week_start=date(2026, 10, 19), phase="base", target_tss=200,
                                     sessions_max=2, days_available=["martes", "jueves", "domingo"],
                                     longest_session_min=120, needs_test=True, zones=zones)
    text = " ".join(errors)
    for expected in ("máximo para este nivel", "fuera de la semana", "no está disponible",
                     "no permitidas", "intensas seguidas", "Falta el test"):
        assert expected in text


def test_validator_accepts_good_week(zones):
    week = {"sessions": [
        _session("2026-10-20", "test", "Z4", 20),
        _session("2026-10-22", minutes=50),
        _session("2026-10-25", "long", "Z2", 90),
    ]}
    target = sum(validator.session_tss(s, zones) for s in week["sessions"])
    errors = validator.validate_week(week, week_start=date(2026, 10, 19), phase="base", target_tss=target,
                                     sessions_max=4, days_available=["martes", "jueves", "domingo"],
                                     longest_session_min=120, needs_test=True, zones=zones)
    assert errors == []


def test_garmin_workout_pace_targets(zones):
    session = {"date": "2026-10-22", "title": "Series", "type": "intervals", "description": "5x3'",
               "cadence": {"target_rpm": 82, "metronome_bpm": 164},
               "steps": [{"kind": "warmup", "duration_min": 10, "zone": "Z1"},
                         {"kind": "repeat", "times": 5, "steps": [
                             {"kind": "interval", "duration_min": 3, "zone": "Z4"},
                             {"kind": "recovery", "duration_min": 2, "zone": "Z1"}]},
                         {"kind": "cooldown", "distance_km": 2, "zone": "Z1"}]}
    w = build_workout(session, zones)
    steps = w["workoutSegments"][0]["workoutSteps"]
    assert [s["stepOrder"] for s in steps] == [1, 2, 5]
    interval = steps[1]["workoutSteps"][0]
    assert interval["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert interval["targetValueOne"] < interval["targetValueTwo"]  # lento < rápido en m/s
    assert steps[2]["endCondition"]["conditionTypeKey"] == "distance"
    assert "164 bpm" in w["description"]
    assert w["estimatedDurationInSecs"] > 45 * 60
