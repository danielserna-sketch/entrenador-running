"""Mapeos de respuestas reales de Garmin (formas confirmadas con sondeo de una cuenta real)."""

from datetime import date

from coach.garmin import mappers


def test_map_scheduled_reads_real_shape():
    raw = {"workoutScheduleId": 987, "calendarDate": "2026-10-19", "workout": {"workoutId": 123,
                                                                              "workoutName": "Series 6x800"}}
    assert mappers.map_scheduled(raw) == {"schedule_id": "987", "workout_id": "123",
                                          "date": "2026-10-19", "title": "Series 6x800"}


def test_map_performance_summarizes_sections():
    out = mappers.map_performance(
        max_metrics=[{"generic": {"calendarDate": "2026-10-09", "vo2MaxValue": 52.0}}],
        race={"time5K": 1200, "time10K": 2550, "timeHalfMarathon": 5700, "timeMarathon": 12000, "userId": 1},
        status={"mostRecentTrainingStatus": {"latestTrainingStatusData": {"42": {"trainingStatus": 4,
                                                                                 "phrase": "PRODUCTIVE",
                                                                                 "nested": {"x": 1}}}}},
        lactate={"speed_and_heart_rate": {"speed": 0.34, "heartRate": 161},
                 "power": {"functionalThresholdPower": 250}},
    )
    assert out["vo2max"] == {"fecha": "2026-10-09", "vo2max": 52.0}
    assert out["prediccion_carrera_s"]["5k"] == 1200
    assert out["estado_entrenamiento"] == {"trainingStatus": 4, "phrase": "PRODUCTIVE"}
    assert out["umbral_lactato"] == {"fc": 161, "ritmo_s_per_km": round(1000 / 3.4, 1), "ftp_w": 250}


def test_map_performance_lactate_speed_comes_in_tens_of_m_per_s():
    # Dato real de Garmin: 0.36666564 corresponde a 3.67 m/s, es decir ~4:33/km.
    out = mappers.map_performance(None, None, None,
                                  {"speed_and_heart_rate": {"speed": 0.36666564, "heartRate": 170}})
    assert out["umbral_lactato"]["ritmo_s_per_km"] == 272.7


def test_map_performance_omits_missing_sections():
    assert mappers.map_performance(None, None, None, None) == {}


def test_map_daily_accepts_readiness_list():
    day = date(2026, 10, 18)
    out = mappers.map_daily(day, {}, {}, {}, [{"score": 71, "calendarDate": "2026-10-18"}])
    assert out["training_readiness"] == 71
