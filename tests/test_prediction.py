"""Predicción de carrera: fórmulas publicadas, combinación con rango y confianza, altura y advertencias."""

from datetime import date

from coach.analytics import prediction as p
from coach.methodology import rules

TODAY = date(2026, 10, 18)


def effort(key, time_s, day="2026-09-20", source="Strava"):
    return {"key": key, "km": p.DISTANCE_KM[key], "time_s": time_s, "date": day, "source": source}


def test_vdot_matches_daniels_tables():
    vdot = p.vdot_from_race(5, 20 * 60)
    assert abs(vdot - 49.8) < 0.1
    assert abs(p.time_from_vdot(vdot, 10) - (41 * 60 + 25)) < 15  # tablas de Daniels: ~41:21-41:28


def test_riegel_and_marathon_exponent():
    assert round(p.riegel(2400, 10, 21.0975)) == round(2400 * 2.10975 ** 1.06)
    assert p.riegel(2400, 10, 42.195) == 2400 * 4.2195 ** rules.RIEGEL_MARATHON_EXPONENT


def test_tanda_formula():
    # Pm = 17.1 + 140·e^(−0.0053·60) + 0.55·330 ≈ 300.5 s/km → ~3:31:18
    assert abs(p.tanda_marathon(60, 330) - (3 * 3600 + 31 * 60 + 18)) < 5


def test_prediction_gives_range_confidence_and_pace():
    out = p.predict("half", [effort("10k", 2880), effort("5k", 1429, "2026-08-01")], today=TODAY)
    low, high = out["rango_s"]
    assert low < out["tiempo_s"] < high
    assert out["confianza"] in ("alta", "media") and out["ritmo"].endswith("/km")
    assert {m["metodo"] for m in out["metodos"]} == {"Riegel", "VDOT (Daniels)"}


def test_disagreement_widens_range_and_lowers_confidence():
    agree = p.predict("10k", [effort("5k", 1429), effort("half", 6300)], today=TODAY)
    disagree = p.predict("10k", [effort("5k", 1429), effort("half", 8400)], today=TODAY)
    width = lambda o: o["rango_s"][1] - o["rango_s"][0]
    assert width(disagree) > width(agree)
    assert disagree["confianza"] == "baja"


def test_old_marks_are_ignored_and_warned():
    out = p.predict("10k", [effort("5k", 1429, "2025-01-01")], today=TODAY)
    assert "error" in out
    out = p.predict("10k", [effort("5k", 1429, "2026-03-01")], today=TODAY)
    assert any("6 meses" in w for w in out["advertencias"])


def test_marathon_with_low_volume_warns_and_uses_tanda():
    training = {"weekly_km": 25, "mean_pace_s": 360, "longest_km": 16, "runs": 24}
    out = p.predict("marathon", [effort("half", 6300)], today=TODAY, training=training)
    assert any("km/semana" in w for w in out["advertencias"])
    assert "Tanda (entrenamiento)" in {m["metodo"] for m in out["metodos"]}


def test_racing_at_sea_level_after_training_at_altitude_is_faster():
    marks = [effort("10k", 2880)]
    home = p.predict("half", marks, today=TODAY, home_altitude_m=2600, race_altitude_m=2600)
    sea = p.predict("half", marks, today=TODAY, home_altitude_m=2600, race_altitude_m=0)
    assert sea["tiempo_s"] < home["tiempo_s"]
    assert abs(sea["tiempo_s"] / home["tiempo_s"] - 1 / p.altitude_factor(2600)) < 0.001
    assert sea["ajuste_altura"].startswith("-")


def test_critical_speed_needs_three_distances():
    efforts = [effort("mile", 400), effort("5k", 1350), effort("10k", 2820)]
    cs = p.critical_speed(efforts)
    assert cs and 280 < cs["cs_pace_s_per_km"] < 300
    assert p.critical_speed(efforts[:2]) is None


def test_training_indices_last_eight_weeks():
    acts = [{"date": "2026-10-10", "sport": "running", "distance_km": 10, "duration_s": 3600},
            {"date": "2026-10-12", "sport": "running", "distance_km": 20, "duration_s": 7500},
            {"date": "2026-06-01", "sport": "running", "distance_km": 30, "duration_s": 10800},
            {"date": "2026-10-11", "sport": "cycling", "distance_km": 40, "duration_s": 5400}]
    out = p.training_indices(acts, TODAY)
    assert out == {"weekly_km": 3.8, "mean_pace_s": 370.0, "longest_km": 20, "runs": 2}


def test_predict_race_tool(repo):
    from coach.agent.tools import ToolContext, predict_race
    from coach.planning.service import PlanService

    class NoGarmin:
        def performance(self, day):
            return {"prediccion_carrera_s": {"media": 6000}}

    repo.update_profile({"best_times": {"10k": 2880}, "home_altitude_m": 2600, "race_altitude_m": 10})
    ctx = ToolContext(repo=repo, plan=PlanService(repo, None, None, NoGarmin, TODAY), garmin_factory=NoGarmin,
                      today=TODAY)
    out = predict_race(ctx, {"distance": "half"})
    assert out["tiempo"] and out["ajuste_altura"]
    assert "Garmin (VO2max)" in {m["metodo"] for m in out["metodos"]}
