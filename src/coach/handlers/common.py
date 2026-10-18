"""Construcción de dependencias compartida por las Lambdas."""

from __future__ import annotations

import logging
from datetime import datetime

from coach.agent.coach import Coach
from coach.config import get_settings
from coach.llm.factory import get_llm
from coach.storage.repo import Repo

logging.getLogger().setLevel(logging.INFO)


def garmin_factory():
    from coach.garmin.unofficial import UnofficialGarmin

    return UnofficialGarmin()


def local_now() -> datetime:
    return datetime.now(get_settings().tz)


def make_coach(task: str = "chat", repo: Repo | None = None) -> Coach:
    return Coach(repo=repo or Repo(), llm=get_llm(task), garmin_factory=garmin_factory, now=local_now())
