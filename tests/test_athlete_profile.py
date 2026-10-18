"""Conocimiento del atleta: ficha, escritura sin pérdidas, memoria editable, compactación y resumen."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from coach.agent import athlete_profile as ap
from coach.agent.coach import COMPACT_AT, KEEP_RAW, Coach
from coach.agent.tools import (
    ToolContext,
    forget_memory,
    get_profile,
    save_memory,
    update_athlete_profile,
    update_memory,
)
from coach.llm.base import StepResult
from coach.planning.service import PlanService

TODAY = date(2026, 10, 18)
NOW = datetime(2026, 10, 18, 9, 30, tzinfo=ZoneInfo("America/Bogota"))
FULL_PROFILE = {"name": "Daniel", "goal_distance": "half", "race_date": "2027-04-04",
                "days_available": ["martes", "jueves", "domingo"], "max_hours_week": 6,
                "longest_session_min": 120, "injuries": "ninguna", "best_times": {"10k": 2880}}


def make_ctx(repo, today=TODAY):
    plan = PlanService(repo, None, None, lambda: None, today)
    return ToolContext(repo=repo, plan=plan, garmin_factory=lambda: None, today=today)


class SummaryLLM:
    """complete_json guionado; session devuelve una respuesta fija y guarda el system recibido."""

    provider = "fake"

    def __init__(self, result=None, fail=False):
        self.result, self.fail, self.calls, self.systems = result, fail, [], []

    def complete_json(self, system, prompt, schema, tier="main"):
        self.calls.append((prompt, tier))
        if self.fail:
            raise RuntimeError("timeout")
        return self.result

    def session(self, system, history, tools, tier="main"):
        self.systems.append((system, history))
        llm = self

        class S:
            def step(self):
                return StepResult(text="ok")

            def add_tool_results(self, results):
                pass

        llm.last_session = S()
        return llm.last_session


def strava_records(seconds, day):
    return {"distances": {"10k": {"last_12_months": {"time_s": seconds, "date": day},
                                  "all_time": {"time_s": seconds, "date": day}}}}


# ---------------------------------------------------------------------------- ficha
def test_empty_profile_lists_everything_missing(repo):
    text = ap.build_ficha(repo, TODAY).to_prompt()
    for label in ("distancia objetivo", "fecha de la carrera", "días disponibles", "horas máximas",
                  "lesiones", "mejores marcas"):
        assert label in text


def test_complete_profile_has_nothing_missing(repo):
    repo.update_profile(FULL_PROFILE)
    ficha = ap.build_ficha(repo, TODAY)
    assert ficha.faltantes == []
    text = ficha.to_prompt()
    assert "Falta saber: nada" in text
    assert "2027-04-04" in text and "martes, jueves, domingo" in text and "48:00" in text


def test_strava_marks_cover_missing_best_times(repo):
    repo.update_profile({k: v for k, v in FULL_PROFILE.items() if k != "best_times"})
    repo.save_records(strava_records(2700, "2026-09-01"))
    ficha = ap.build_ficha(repo, TODAY)
    assert "mejores marcas recientes" not in ficha.faltantes
    assert "45:00 (Strava, últimos 12 meses, 2026-09-01)" in ficha.to_prompt()


def test_conflict_is_flagged_until_athlete_reconfirms(repo):
    repo.update_profile(FULL_PROFILE)  # 10K 48:00 sin fecha (forma antigua)
    repo.save_records(strava_records(2700, "2026-09-01"))  # 45:00 en Strava, más reciente
    assert ap.build_ficha(repo, TODAY).conflictos == ["Marca 10K: perfil 48:00, Strava 45:00 (2026-09-01)"]

    update_athlete_profile(make_ctx(repo), {"best_times": {"10k": 2880}})  # el atleta la reconfirma
    assert ap.build_ficha(repo, TODAY).conflictos == []


def test_notes_detail_last_14_days_and_summarize_older(repo):
    repo.add_note({"date": "2026-10-16", "rpe": 7, "pain": 3, "pain_location": "rodilla", "text": "pesada"})
    repo.add_note({"date": "2026-09-20", "rpe": 5})
    repo.add_note({"date": "2026-09-10", "rpe": 6, "pain": 2, "pain_location": "gemelo"})
    text = ap.build_ficha(repo, TODAY).to_prompt()
    assert "2026-10-16: RPE 7, dolor 3/10 rodilla, pesada" in text
    assert "2 notas anteriores, RPE medio 5.5, dolor reportado 1 veces (gemelo)" in text
    assert "2026-09-20:" not in text


def test_ficha_stays_within_budget_with_lots_of_data(repo):
    repo.update_profile(FULL_PROFILE)
    repo.save_memory({"facts": [{"date": f"2026-0{1 + i % 9}-01", "fact": f"Hecho número {i} " + "x" * 150}
                                for i in range(60)], "summary": ""})
    for i in range(200):
        repo.add_note({"date": f"2026-10-{1 + i % 18:02d}", "rpe": i % 10, "text": "nota " + "y" * 80})
    text = ap.build_ficha(repo, TODAY).to_prompt()
    assert len(text) <= ap.FICHA_MAX_CHARS + 100
    assert "consulta get_profile antes de preguntar" in text


# ---------------------------------------------------------------------------- formas antiguas
def test_legacy_shapes_are_normalized():
    assert ap.normalize_best_times({"10k": 2880, "5k": None}) == {
        "10k": {"tiempo_s": 2880, "fecha": None, "fuente": "atleta"}}
    assert ap.normalize_entries("rodilla 2024") == [{"fecha": None, "texto": "rodilla 2024"}]
    memory = ap.normalize_memory({"facts": [{"date": "2026-01-01", "fact": "a"}, {"id": "1", "fact": "b"}]})
    assert [f["id"] for f in memory["facts"]] == ["2", "1"]
    assert memory["summary"] == ""


# ---------------------------------------------------------------------------- escritura sin pérdidas
def test_injuries_and_notes_accumulate_with_dates(repo):
    repo.update_profile({"injuries": "fascitis en 2024"})  # forma antigua
    update_athlete_profile(make_ctx(repo), {"injuries": "molestia en la rodilla derecha"})
    injuries = repo.get_profile()["injuries"]
    assert injuries == [{"fecha": None, "texto": "fascitis en 2024"},
                        {"fecha": "2026-10-18", "texto": "molestia en la rodilla derecha"}]


def test_best_times_keep_date_and_source(repo):
    update_athlete_profile(make_ctx(repo), {"best_times": {"5k": 1429}})
    assert repo.get_profile()["best_times"]["5k"] == {"tiempo_s": 1429, "fecha": "2026-10-18", "fuente": "atleta"}


def test_memory_update_and_forget_with_confirmation(repo):
    ctx = make_ctx(repo)
    fact_id = save_memory(ctx, {"fact": "Corre en la mañana antes del trabajo"})["id"]
    assert update_memory(ctx, {"id": fact_id, "fact": "Corre al mediodía"})["ok"]

    refused = forget_memory(ctx, {"id": fact_id, "confirmed_by_athlete": False})
    assert "confirmación" in refused["error"]
    assert forget_memory(ctx, {"id": fact_id, "confirmed_by_athlete": True, "reason": "ya no aplica"})["ok"]

    assert get_profile(ctx, {})["memoria"]["hechos"] == []
    assert "Corre al mediodía" not in ap.build_ficha(repo, TODAY).to_prompt()
    stored = repo.get_memory()["facts"][0]
    assert stored["olvidado"] == "2026-10-18" and stored["fact"] == "Corre al mediodía"  # queda auditado


def test_memory_cap_drops_forgotten_first():
    facts = [{"id": str(i), "fact": str(i), "olvidado": "x" if i < 5 else None} for i in range(ap.MEMORY_MAX_FACTS + 3)]
    capped = ap.cap_facts(facts)
    assert len(capped) == ap.MEMORY_MAX_FACTS
    assert [f["id"] for f in capped[:2]] == ["3", "4"]


# ---------------------------------------------------------------------------- prompt
def test_system_has_ficha_and_message_has_date(repo):
    repo.update_profile(FULL_PROFILE)
    llm = SummaryLLM()
    Coach(repo=repo, llm=llm, garmin_factory=lambda: None, now=NOW).run("¿qué hago hoy?")
    system, history = llm.systems[0]
    assert "FICHA DEL ATLETA" in system.dynamic and "Falta saber: nada" in system.dynamic
    assert "09:30" not in system.dynamic and "09:30" not in system.static
    assert history[-1].text.startswith("[Contexto del sistema: hoy domingo 2026-10-18, hora local 09:30]")


# ---------------------------------------------------------------------------- compactación
def seed_messages(repo, n):
    for i in range(n):
        repo.add_message("user" if i % 2 == 0 else "assistant", f"mensaje {i}")


def test_history_is_compacted_and_anchored(repo):
    seed_messages(repo, COMPACT_AT + 6)
    llm = SummaryLLM(result={"resumen": "Daniel prefiere correr temprano."})
    coach = Coach(repo=repo, llm=llm, garmin_factory=lambda: None, now=NOW)

    history = coach._history()
    assert [m.text for m in history] == [f"mensaje {i}" for i in range(COMPACT_AT + 6 - KEEP_RAW, COMPACT_AT + 6)]
    state = repo.get_conversation()
    assert state["summary"] == "Daniel prefiere correr temprano." and state["anchor"]
    assert llm.calls[0][1] == "fast"

    repo.add_message("user", "nuevo")
    assert len(coach._history()) == KEEP_RAW + 1 and len(llm.calls) == 1  # sin otra compactación
    assert "Daniel prefiere correr temprano." in coach._system().dynamic


def test_compaction_failure_keeps_recent_messages(repo):
    seed_messages(repo, COMPACT_AT + 6)
    coach = Coach(repo=repo, llm=SummaryLLM(fail=True), garmin_factory=lambda: None, now=NOW)
    assert len(coach._history()) == COMPACT_AT
    assert repo.get_conversation() == {}


# ---------------------------------------------------------------------------- resumen semanal
def test_weekly_summary_saves_summary_and_only_new_facts(repo):
    repo.update_profile(FULL_PROFILE)
    save_memory(make_ctx(repo), {"fact": "Trabaja de lunes a viernes"})
    repo.add_message("user", "Esta semana me cambiaron el horario, ahora salgo a las 7")
    llm = SummaryLLM(result={"resumen": "Corredor de media maratón, objetivo abril.",
                             "hechos_nuevos": ["trabaja de lunes a viernes", "Sale del trabajo a las 7 pm"]})

    out = ap.refresh_athlete_summary(repo, llm, TODAY)
    assert out["hechos_nuevos"] == ["Sale del trabajo a las 7 pm"]
    memory = ap.normalize_memory(repo.get_memory())
    assert memory["summary"] == "Corredor de media maratón, objetivo abril."
    assert memory["facts"][-1]["fuente"] == "resumen_semanal"
    assert "me cambiaron el horario" in llm.calls[0][0]


@pytest.mark.parametrize("value", [None, "", {}])
def test_missing_best_times_values_are_ignored(value):
    assert ap.normalize_best_times({"10k": value}) == {}
