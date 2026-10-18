from datetime import date, timedelta

from conftest import make_activity

from coach.analytics import alerts, load


def test_rtss_one_hour_at_threshold_is_100():
    assert round(load.rtss(3600, 294, 294)) == 100


def test_activity_load_prefers_rtss(zones):
    out = load.activity_load(make_activity(date(2026, 10, 1), minutes=60, pace_s=294), zones)
    assert out["tss_method"] == "rTSS" and round(out["tss"]) == 100 and out["if"] == 1.0


def test_hrtss_fallback():
    out = load.activity_load({"duration_s": 3600, "avg_hr": 161.5}, {"threshold_hr": 161.5})
    assert out["tss_method"] == "hrTSS" and round(out["tss"]) == 100


def test_acwr_and_fitness(history):
    today = date(2026, 10, 16)
    ratio = load.acwr(history, today)
    assert 0.8 < ratio < 1.3
    series = load.fitness_series(history, today - timedelta(days=60), today)
    assert series[-1]["ctl"] > 0


def test_acwr_spike_triggers_alert(history):
    today = date(2026, 10, 16)
    spiked = history + [make_activity(today - timedelta(days=i), tss=150, activity_id=f"x{i}") for i in range(4)]
    kinds = {a.kind for a in alerts.evaluate(spiked, [], [], [], today)}
    assert "acwr_high" in kinds


def test_wellness_and_pain_alerts():
    today = date(2026, 10, 16)
    dailies = [{"date": (today - timedelta(days=i)).isoformat(), "hrv_last_night": 60 + (i % 3),
                "resting_hr": 48, "sleep_score": 80} for i in range(2, 30)]
    dailies += [{"date": (today - timedelta(days=i)).isoformat(), "hrv_last_night": 40, "resting_hr": 56,
                 "sleep_score": 50} for i in range(2)]
    notes = [{"date": today.isoformat(), "pain": 6, "pain_location": "rodilla"}]
    kinds = {a.kind for a in alerts.evaluate([], dailies, notes, [], today)}
    assert {"hrv_low", "rhr_high", "pain"} <= kinds
