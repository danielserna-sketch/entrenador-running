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


GARMIN_FLOORS = [111, 145, 151, 162, 170]


class FakeZonesGarmin:
    def __init__(self, data=None, fail=False):
        self.data, self.fail, self.calls = data, fail, 0

    def running_hr_zones(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Garmin caído")
        return self.data


def test_ensure_zones_uses_garmin_hr_zones(repo):
    garmin = FakeZonesGarmin({"floors": GARMIN_FLOORS, "max_hr": 190, "lthr": None})
    zones = assessment.ensure_zones(repo, garmin, date(2026, 10, 18))
    hr = {z["zone"]: (z["min_bpm"], z["max_bpm"]) for z in zones["hr"]}
    assert hr["Z1"] == (111, 144) and hr["Z2"] == (145, 150) and hr["Z3"] == (151, 161) and hr["Z4"] == (162, 169)
    assert hr["Z5a"] == hr["Z5c"] == (170, 190)
    assert zones["threshold_hr"] == 169 and zones["pace"] == []
    assert assessment.needs_threshold_test(zones, date(2026, 10, 18))  # el ritmo del método sale del test
    assessment.ensure_zones(repo, garmin, date(2026, 10, 18))
    assert garmin.calls == 1  # una sincronización al día


def test_ensure_zones_falls_back_when_garmin_fails(repo):
    zones = assessment.ensure_zones(repo, FakeZonesGarmin(fail=True), date(2026, 10, 18))
    assert [z["min_bpm"] for z in zones["hr"][:5]] == GARMIN_FLOORS
    assert zones["pace"] == []


def test_workout_targets_and_notes_with_garmin_zones(repo):
    from coach.analytics.zones import pace_zones

    zones = assessment.ensure_zones(repo, FakeZonesGarmin({"floors": GARMIN_FLOORS}), date(2026, 10, 18))
    zones = {**zones, "pace": pace_zones(300)}  # ritmo de un test
    session = {"date": "2026-10-20", "title": "3x8 tempo", "type": "tempo",
               "purpose": "Subir el ritmo que aguantas sin ahogarte.", "description": "Respiración agitada pero controlada.",
               "steps": [{"kind": "warmup", "duration_min": 15, "zone": "Z1"},
                         {"kind": "repeat", "times": 3, "steps": [
                             {"kind": "interval", "duration_min": 8, "zone": "Z3", "note": "RPE 7/10"},
                             {"kind": "recovery", "duration_min": 3, "zone": "Z1"}]},
                         {"kind": "cooldown", "duration_min": 10, "zone": "Z1"}]}
    w = build_workout(session, zones)
    steps = w["workoutSegments"][0]["workoutSteps"]
    assert steps[0]["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert (steps[0]["targetValueOne"], steps[0]["targetValueTwo"]) == (111, 144)
    interval = steps[1]["workoutSteps"][0]
    assert interval["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert interval["description"].startswith("Z3 · 151-161 ppm · ") and interval["description"].endswith("RPE 7/10")
    notes = w["description"]
    assert notes.startswith("Objetivo: Subir el ritmo")
    assert "Cómo hacerla: Respiración agitada" in notes
    assert "• Calentamiento 15′ · Z1 · 111-144 ppm" in notes and "• 3 veces:" in notes
    assert "Intervalo 8′ · Z3 · 151-161 ppm" in notes


def test_workout_only_hr_zones_targets_hr_everywhere(repo):
    zones = assessment.ensure_zones(repo, FakeZonesGarmin({"floors": GARMIN_FLOORS}), date(2026, 10, 18))
    session = {"date": "2026-10-20", "title": "Series", "type": "intervals", "description": "",
               "steps": [{"kind": "interval", "duration_min": 3, "zone": "Z5a"}]}
    w = build_workout(session, zones)
    step = w["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone" and step["targetValueOne"] == 170
    assert "Z5a · más de 169 ppm" in w["description"]


def test_workout_without_zones_says_so():
    w = build_workout({"date": "2026-10-20", "title": "Rodaje", "type": "easy", "description": "Suave",
                       "steps": [{"kind": "main", "duration_min": 30, "zone": "Z2"}]}, None)
    assert w["workoutSegments"][0]["workoutSteps"][0]["targetType"]["workoutTargetTypeKey"] == "no.target"
    assert "Aún no hay zonas" in w["description"]


def test_threshold_test_keeps_garmin_hr_zones(repo):
    from types import SimpleNamespace

    from coach.agent import tools

    assessment.ensure_zones(repo, FakeZonesGarmin({"floors": GARMIN_FLOORS}), date(2026, 10, 18))
    pushed = []
    ctx = SimpleNamespace(repo=repo, today=date(2026, 10, 25),
                          garmin=SimpleNamespace(update_running_hr_zones=lambda *a: pushed.append(a)),
                          plan=SimpleNamespace(add_changelog=lambda text: None))
    result = tools.recalculate_zones(ctx, {"test_avg_pace": "4:40", "test_avg_hr": 175})
    zones = result["zonas"]
    assert zones["source"] == "test 20 min" and zones["threshold_pace"] == "4:54"
    assert zones["hr"][1] == {"zone": "Z2", "min_bpm": 145, "max_bpm": 150} and zones["threshold_hr"] == 169
    assert pushed == []  # no se pisan las zonas de FC del atleta en Garmin
    assert not assessment.needs_threshold_test(zones, date(2026, 10, 25))
