import itertools
from datetime import date

from coach.analytics import cadence, compliance
from coach.analytics import zones as z
from coach.methodology import rules


def test_thresholds_from_20min_test():
    th = z.thresholds_from_test(z.pace_from_str("4:40"), 170)
    assert z.pace_to_str(th["threshold_pace_s"]) == "4:54"
    assert th["threshold_hr"] == 161.5


def test_friel_zones_are_contiguous(zones):
    hr = zones["hr"]
    assert [x["zone"] for x in hr] == rules.ZONE_ORDER
    for a, b in itertools.pairwise(hr):
        assert b["min_bpm"] == a["max_bpm"] + 1
    pace = zones["pace"]
    assert pace[3]["zone"] == "Z4" and pace[3]["fast_s"] == 294  # Z4 empieza en el umbral
    assert z.zone_for_pace(300, zones) == "Z4"
    assert z.zone_for_hr(150, zones) in ("Z2", "Z3")


def test_threshold_change_detection(zones):
    assert z.detect_threshold_change(zones, {"threshold_pace_s": 295.0}) is None
    change = z.detect_threshold_change(zones, {"threshold_pace_s": 285.0})
    assert change["threshold_pace_s"]["new"] == 285.0


def test_frequency_table():
    assert rules.sessions_per_week("half", 2 * 3600 + 50 * 60) == (3, 3)
    assert rules.sessions_per_week("half", 1 * 3600 + 45 * 60) == (4, 4)
    assert rules.sessions_per_week("half", 1 * 3600 + 38 * 60) == (4, 5)
    assert rules.sessions_per_week("10k", 39 * 60) == (5, 5)
    assert rules.sessions_per_week("5k", 16 * 60) == (6, 6)


def test_half_level_from_10k():
    level = rules.half_level(ten_k_s=48 * 60)
    assert level.name == "SUB 1h40" and level.sessions == 5
    assert rules.half_level(half_s=2 * 3600 + 5 * 60).name == "SUB 1h55"


def test_compliance_colors():
    assert compliance.color_for_ratio(0.85) == compliance.GREEN
    assert compliance.color_for_ratio(1.20) == compliance.GREEN
    assert compliance.color_for_ratio(0.6) == compliance.YELLOW
    assert compliance.color_for_ratio(1.4) == compliance.YELLOW
    assert compliance.color_for_ratio(0.3) == compliance.ORANGE
    assert compliance.color_for_ratio(1.7) == compliance.ORANGE


def test_session_compliance_red_and_gray():
    planned = {"date": "2026-10-10", "tss": 50}
    assert compliance.session_compliance(planned, None, date(2026, 10, 12))["color"] == compliance.RED
    assert compliance.session_compliance(planned, None, date(2026, 10, 9))["color"] is None
    assert compliance.session_compliance(None, {"date": "2026-10-10"})["color"] == compliance.GRAY
    assert compliance.session_compliance(planned, {"tss": 42.5})["color"] == compliance.GREEN


def test_metronome_from_method_example():
    target = cadence.metronome_target(80)
    assert target["target_rpm"] == 82 and target["metronome_bpm"] == 164
