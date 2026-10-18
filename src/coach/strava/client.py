"""Cliente de solo lectura de la API v3 de Strava.

El access token dura 6 h: se refresca con el refresh token antes de vencer y, como Strava
puede rotar el refresh token, el par nuevo se vuelve a guardar en Secrets Manager.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import httpx

log = logging.getLogger(__name__)

API = "https://www.strava.com/api/v3"
TOKEN_URL = "https://www.strava.com/oauth/token"
REFRESH_MARGIN_S = 300


class StravaAuthError(RuntimeError):
    """El token de Strava fue revocado o rechazado: hay que correr scripts/strava_login.py."""


class StravaNotConfigured(StravaAuthError):
    """Aún no se corrió scripts/strava_login.py (el secreto tiene los valores de ejemplo)."""


class StravaRateLimited(RuntimeError):
    """Se alcanzó el límite de lecturas de Strava (por 15 min o por día)."""


class StravaClient:
    def __init__(self, tokens: dict | None = None, save: Callable[[dict], None] | None = None,
                 http: httpx.Client | None = None):
        if tokens is None:
            from coach.strava import token_store

            tokens, save = token_store.load_tokens(), save or token_store.save_tokens
        if tokens.get("refresh_token") in (None, "", "REEMPLAZAR"):
            raise StravaNotConfigured("Falta conectar Strava: corre scripts/strava_login.py")
        self.tokens = tokens
        self._save = save
        self.http = http or httpx.Client(timeout=30)
        # [uso, límite] de la ventana de 15 min y del día, según los encabezados de la última respuesta
        self.usage: tuple[int, int] | None = None
        self.limit: tuple[int, int] | None = None

    def close(self) -> None:
        self.http.close()

    # ---- autenticación -------------------------------------------------------
    def _access_token(self) -> str:
        if float(self.tokens.get("expires_at") or 0) - time.time() < REFRESH_MARGIN_S:
            self._refresh()
        return self.tokens["access_token"]

    def _refresh(self) -> None:
        resp = self.http.post(TOKEN_URL, data={
            "client_id": self.tokens["client_id"],
            "client_secret": self.tokens["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": self.tokens["refresh_token"],
        })
        if resp.status_code in (400, 401):
            raise StravaAuthError(resp.text)
        resp.raise_for_status()
        data = resp.json()
        self.tokens = {**self.tokens, "access_token": data["access_token"],
                       "refresh_token": data["refresh_token"], "expires_at": data["expires_at"]}
        if self._save:
            self._save(self.tokens)

    # ---- llamadas -------------------------------------------------------------
    def _get(self, path: str, params: dict | None = None):
        resp = self.http.get(f"{API}{path}", params=params,
                             headers={"Authorization": f"Bearer {self._access_token()}"})
        self._read_limits(resp.headers)
        if resp.status_code == 401:
            raise StravaAuthError(resp.text)
        if resp.status_code == 429:
            raise StravaRateLimited(resp.text)
        resp.raise_for_status()
        return resp.json()

    def _read_limits(self, headers: httpx.Headers) -> None:
        # Strava separa las lecturas (X-ReadRateLimit-*) del total (X-RateLimit-*).
        usage = headers.get("X-ReadRateLimit-Usage") or headers.get("X-RateLimit-Usage")
        limit = headers.get("X-ReadRateLimit-Limit") or headers.get("X-RateLimit-Limit")
        if usage and limit:
            self.usage = tuple(int(x) for x in usage.split(","))[:2]  # type: ignore[assignment]
            self.limit = tuple(int(x) for x in limit.split(","))[:2]  # type: ignore[assignment]

    def budget_left(self) -> tuple[float, float]:
        """Fracción libre de la ventana de 15 min y del día (1.0 si aún no hay encabezados)."""
        if not self.usage or not self.limit:
            return 1.0, 1.0
        return tuple(max(0.0, 1 - u / lim) for u, lim in zip(self.usage, self.limit))  # type: ignore[return-value]

    def athlete(self) -> dict:
        return self._get("/athlete")

    def athlete_stats(self, athlete_id: int | str) -> dict:
        return self._get(f"/athletes/{athlete_id}/stats")

    def athlete_zones(self) -> dict:
        return self._get("/athlete/zones")

    def list_activities(self, *, after: int | None = None, before: int | None = None,
                        page: int = 1, per_page: int = 200) -> list[dict]:
        params = {"page": page, "per_page": per_page}
        if after is not None:
            params["after"] = after
        if before is not None:
            params["before"] = before
        return self._get("/athlete/activities", params)

    def activity(self, activity_id: int | str) -> dict:
        """Detalle con best_efforts, splits_metric y laps."""
        return self._get(f"/activities/{activity_id}", {"include_all_efforts": "false"})

    def gear(self, gear_id: str) -> dict:
        return self._get(f"/gear/{gear_id}")
