import json
import time
from datetime import date, datetime, timedelta

import httpx
import pytest

from coach.analytics import history
from coach.planning import assessment
from coach.strava import mappers
from coach.strava import sync as strava_sync
from coach.strava.client import StravaAuthError, StravaClient

TODAY = date(2026, 10, 17)


def raw_run(i: int, day: date, km: float = 10.0, pace_s: float = 300, sport: str = "Run",
            workout_type: int = 0, external_id: str | None = None) -> dict:
    start = datetime(day.year, day.month, day.day, 6, 0)
    return {
        "id": 1000 + i, "name": f"Carrera {i}", "sport_type": sport, "type": sport,
        "start_date": start.isoformat() + "Z", "start_date_local": start.isoformat() + "Z",
        "distance": km * 1000, "moving_time": int(km * pace_s), "elapsed_time": int(km * pace_s) + 30,
        "average_speed": 1000 / pace_s, "average_heartrate": 150, "max_heartrate": 170,
        "average_cadence": 84, "total_elevation_gain": 40, "workout_type": workout_type,
        "external_id": external_id, "gear_id": "g1",
    }


def with_efforts(raw: dict, ten_k_s: int, five_k_s: int) -> dict:
    return {**raw, "best_efforts": [
        {"name": "5K", "elapsed_time": five_k_s, "start_date_local": raw["start_date_local"], "pr_rank": None},
        {"name": "10K", "elapsed_time": ten_k_s, "start_date_local": raw["start_date_local"], "pr_rank": None},
        {"name": "Nombre raro", "elapsed_time": 1},
    ], "splits_metric": [{"split": 1, "distance": 1000, "moving_time": 300, "average_speed": 3.33,
                          "average_grade_adjusted_speed": 3.4, "average_heartrate": 148}]}


class FakeStrava:
    """API de Strava en memoria con límite de lecturas por ventana de 15 min."""

    def __init__(self, activities: list[dict], limit_15: int = 1000):
        self.activities = sorted(activities, key=lambda a: a["start_date"])
        self.limit_15 = limit_15
        self.usage = 0
        self.refreshes = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            self.refreshes += 1
            return httpx.Response(200, json={"access_token": "nuevo", "refresh_token": "r2",
                                             "expires_at": time.time() + 21600})
        self.usage += 1
        headers = {"X-ReadRateLimit-Usage": f"{self.usage},{self.usage}",
                   "X-ReadRateLimit-Limit": f"{self.limit_15},1000"}
        path = request.url.path.removeprefix("/api/v3")
        if path == "/athlete/activities":
            after = int(request.url.params.get("after", 0))
            per_page = int(request.url.params.get("per_page", 30))
            items = [a for a in self.activities
                     if datetime.fromisoformat(a["start_date"]).timestamp() > after][:per_page]
            return httpx.Response(200, json=items, headers=headers)
        if path.startswith("/activities/"):
            activity_id = int(path.rsplit("/", 1)[1])
            raw = next(a for a in self.activities if a["id"] == activity_id)
            return httpx.Response(200, json=with_efforts(raw, 3000 + activity_id % 100, 1450 + activity_id % 50),
                                  headers=headers)
        return httpx.Response(404, headers=headers)

    def client(self) -> StravaClient:
        tokens = {"client_id": "1", "client_secret": "s", "refresh_token": "r",
                  "access_token": "a", "expires_at": time.time() + 3600}
        return StravaClient(tokens, http=httpx.Client(transport=httpx.MockTransport(self.handler)))


# ---------------------------------------------------------------------------- mapeo
def test_map_activity_and_efforts():
    raw = with_efforts(raw_run(1, date(2026, 3, 1), external_id="garmin_ping_123456789"), 2900, 1400)
    a = mappers.map_detail(raw)
    assert a["sport"] == "running" and a["source"] == "strava"
    assert a["date"] == "2026-03-01" and a["distance_km"] == 10.0
    assert a["avg_pace_s_per_km"] == 300.0
    assert a["avg_cadence_spm"] == 168  # Strava da zancadas de una pierna
    assert a["from_garmin"] and strava_sync._garmin_id(a["external_id"]) == "123456789"
    assert {e["distance"] for e in a["best_efforts"]} == {"5k", "10k"}
    assert a["best_efforts"][1]["pace_s_per_km"] == 290.0
    assert a["splits"][0]["pace_s_per_km"] == 300.3

    ride = mappers.map_activity(raw_run(2, date(2026, 3, 2), sport="Ride"))
    assert ride["sport"] == "ride" and ride["avg_cadence_spm"] == 84


# ---------------------------------------------------------------------------- cliente
def test_client_refreshes_expired_token_and_saves_it():
    fake = FakeStrava([raw_run(1, date(2026, 1, 1))])
    saved = []
    client = fake.client()
    client.tokens["expires_at"] = 0
    client._save = saved.append
    client.list_activities()
    assert fake.refreshes == 1
    assert saved[-1]["refresh_token"] == "r2" and saved[-1]["client_secret"] == "s"
    assert client.budget_left()[0] == pytest.approx(0.999)


def test_client_auth_error_on_401():
    client = StravaClient({"client_id": "1", "client_secret": "s", "refresh_token": "r", "access_token": "a",
                           "expires_at": time.time() + 3600},
                          http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    with pytest.raises(StravaAuthError):
        client.athlete()


# ---------------------------------------------------------------------------- sincronización
def test_backfill_stops_on_budget_and_resumes(repo, monkeypatch):
    monkeypatch.setattr(strava_sync, "PER_PAGE", 10)
    start = date(2024, 1, 1)
    raws = [raw_run(i, start + timedelta(days=3 * i)) for i in range(25)]
    raws.append(raw_run(99, start + timedelta(days=1), sport="Ride"))
    fake = FakeStrava(raws, limit_15=20)

    first = strava_sync.sync(fake.client(), repo, TODAY)
    assert first["stopped"] == "límite de Strava"
    assert not first["backfill_done"] and first["pending"] > 0
    assert len(repo.strava_activities()) == 26  # el listado completo cabe en la primera corrida

    for _ in range(10):  # nueva ventana de 15 min en cada corrida
        fake.usage = 0
        result = strava_sync.sync(fake.client(), repo, TODAY)
        if result["backfill_done"]:
            break
    assert result["backfill_done"] and result["pending"] == 0
    runs = [a for a in repo.strava_activities() if a["sport"] == "running"]
    assert len(runs) == 25 and all(a.get("detailed") for a in runs)

    records = repo.get_records()["distances"]
    assert records["10k"]["all_time"]["time_s"] == 3000  # id 1000 → 3000 + 0
    assert records["5k"]["all_time"]["time_s"] == 1450

    # Incremental: una carrera nueva se lista y se detalla sin repetir lo anterior.
    fake.activities.append(raw_run(200, date(2026, 10, 16)))
    fake.usage = 0
    inc = strava_sync.sync(fake.client(), repo, TODAY)
    assert inc["listed"] == 1 and inc["detailed"] == 1
    assert repo.get_strava_activity("2026-10-16", "1200")["detailed"]


# ---------------------------------------------------------------------------- analítica
def _act(day: str, km: float, pace: float, efforts: dict | None = None, workout_type: str = "normal") -> dict:
    return {"activity_id": day, "date": day, "sport": "running", "distance_km": km, "duration_s": km * pace,
            "avg_pace_s_per_km": pace, "avg_hr": 145, "workout_type": workout_type,
            "best_efforts": [{"distance": k, "elapsed_s": v, "pace_s_per_km": v / mappers.EFFORT_KM[k],
                              "date": day, "activity_id": day} for k, v in (efforts or {}).items()]}


def test_records_trends_and_summary():
    acts = [
        _act("2023-05-01", 10, 290, {"10k": 2850, "5k": 1390}, "carrera"),
        _act("2026-02-01", 10, 300, {"10k": 2950}),
        _act("2026-02-15", 21.1, 330, {"half": 7000, "10k": 3200}, "fondo"),
        _act("2026-03-20", 8, 340),
    ]
    rec = history.personal_records(acts, TODAY)["distances"]
    assert rec["10k"]["all_time"]["time_s"] == 2850
    assert rec["10k"]["last_12_months"]["time_s"] == 2950
    assert list(rec["10k"]["best_by_year"]) == ["2023", "2026"]
    assert list(history.personal_records(acts, TODAY)["distances"])[:2] == ["5k", "10k"]

    ref = history.best_times_from_records({"distances": rec})
    assert ref["10k"]["time_s"] == 2950 and ref["5k"] == {"time_s": 1390, "date": "2023-05-01", "recent": False}

    trends = history.pace_trends(acts)
    feb = next(t for t in trends if t["period"] == "2026-02")
    assert feb["runs"] == 2 and feb["km"] == 31.1 and feb["longest_run_km"] == 21.1
    assert feb["easy_pace_s_per_km"] == 300.0  # el fondo no cuenta como rodaje suave

    summary = history.history_summary(acts)
    assert summary["runs"] == 4 and summary["longest_run"]["km"] == 21.1
    assert summary["breaks_over_3_weeks"][0]["from"] == "2023-05-01"

    fastest = history.search(acts, min_km=9, sort="pace", limit=1)
    assert fastest[0]["date"] == "2023-05-01"


def test_assessment_uses_strava_when_profile_has_no_marks():
    records = {"distances": history.personal_records(
        [_act("2026-06-01", 10, 300, {"10k": 2900})], TODAY)["distances"]}
    t, source = assessment.reference_time({"goal_distance": "10k"}, "10k", records)
    assert t == 2900 and source == "marca Strava (2026-06-01)"
    # Lo que el atleta confirmó en el perfil manda sobre Strava.
    t, source = assessment.reference_time({"best_times": {"10k": 2800}}, "10k", records)
    assert (t, source) == (2800, "marca real")
    t, source = assessment.reference_time({}, "half", records)
    assert source.startswith("equivalente desde 10k") and t > 2900 * 2


def test_record_tool(repo):
    from coach.agent.tools import ToolContext, run_tool

    ctx = ToolContext(repo=repo, plan=None, garmin_factory=None, today=TODAY)
    out, is_error = run_tool(ctx, "get_personal_records", {})
    assert not is_error and "Strava" in json.loads(out)["error"]

    repo.save_strava_sync({"listed_until": 1, "pending": [], "backfill_done": True})
    repo.save_strava_activity(_act("2026-06-01", 10, 300, {"10k": 2900}))
    repo.save_records(history.personal_records(repo.strava_activities(), TODAY))
    out, is_error = run_tool(ctx, "get_personal_records", {"distance": "10k"})
    data = json.loads(out)
    assert not is_error and data["records"]["10k"]["all_time"]["time"] == "48:20"
    assert data["records"]["10k"]["all_time"]["pace"] == "4:50"

    out, is_error = run_tool(ctx, "get_training_history", {})
    assert not is_error and json.loads(out)["por_periodo"][0]["period"] == "2026"


# ---------------------------------------------------------------------------- archivo exportado
def test_best_efforts_from_track():
    from coach.strava.export import best_efforts, km_splits

    # 6 km: 3 km a 5:00/km y luego 3 km a 4:00/km, un punto por segundo
    track, t, d = [], 0.0, 0.0
    for speed, secs in ((1000 / 300, 900), (1000 / 240, 720)):
        for _ in range(secs):
            t += 1
            d += speed
            track.append((t, d, 150))
    efforts = best_efforts([(0.0, 0.0, None), *track])
    assert efforts["1k"] == pytest.approx(240, abs=1)
    assert efforts["5k"] == pytest.approx(3 * 240 + 2 * 300, abs=1)
    assert "10k" not in efforts
    splits = km_splits(track)
    assert len(splits) >= 5 and splits[0]["pace_s_per_km"] == pytest.approx(300, abs=1)
    assert splits[-1]["avg_hr"] == 150


def _export_csv(rows: list[list[str]]) -> str:
    import csv
    import io

    header = [""] * 103
    header[0], header[3] = "ID de actividad", "Tipo de actividad"
    buf = io.StringIO()
    csv.writer(buf).writerows([header, *rows])
    return buf.getvalue()


def _row(**values) -> list[str]:
    from coach.strava.export import COL

    row = [""] * 103
    for key, value in values.items():
        row[COL[key]] = str(value)
    return row


def test_import_export_folder(tmp_path):
    from zoneinfo import ZoneInfo

    from coach.strava.export import iter_activities, parse_date

    assert parse_date("20 sept 2026, 13:13:35").isoformat() == "2026-09-20T13:13:35+00:00"
    assert parse_date("Sep 20, 2026, 1:13:35 PM").hour == 13

    # GPX de 1.2 km hacia el norte, 1 punto cada 3 s a ~3.33 m/s
    pts = "".join(
        f'<trkpt lat="{4.9 + i * 10 / 111195:.7f}" lon="-74.0"><time>2026-09-20T13:{13 + (i * 3) // 60:02d}:'
        f'{(35 + i * 3) % 60:02d}Z</time><extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>150'
        f'</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>'
        for i in range(121))
    (tmp_path / "activities").mkdir()
    (tmp_path / "activities" / "1.gpx").write_text(
        '<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"><trk><trkseg>'
        f"{pts}</trkseg></trk></gpx>")
    (tmp_path / "activities.csv").write_text(_export_csv([
        _row(id="77", date="20 sept 2026, 13:13:35", name="Rodaje", type="Carrera", filename="activities/1.gpx",
             elapsed_s="370.0", moving_s="360.0", distance_m="1200.0", avg_speed="3.333", avg_cadence="82.0",
             avg_hr="150.0", relative_effort="12.0"),
        _row(id="78", date="21 sept 2026, 0:30:00", name="Bici", type="Bicicleta", distance_m="20000.0",
             moving_s="3600.0", avg_speed="5.55", avg_cadence="85.0"),
    ]))

    run, ride = list(iter_activities(tmp_path, ZoneInfo("America/Bogota")))
    assert run["sport"] == "running" and run["date"] == "2026-09-20" and run["start_local"] == "2026-09-20T08:13:35"
    assert run["distance_km"] == 1.2 and run["avg_pace_s_per_km"] == 300.0 and run["avg_cadence_spm"] == 164
    assert run["detailed"] and run["best_efforts"][0]["distance"] == "400m"
    assert {e["distance"] for e in run["best_efforts"]} >= {"400m", "1k"}
    assert run["best_efforts"][-1]["elapsed_s"] == pytest.approx(300, abs=3)
    # Una actividad de la noche en UTC cae el día anterior en hora local.
    assert ride["sport"] == "ride" and ride["date"] == "2026-09-20" and ride["avg_cadence_spm"] == 85
