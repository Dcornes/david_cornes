"""A small Strava API client: your ski days and their GPS tracks.

Needs STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET and STRAVA_REFRESH_TOKEN (backend/.env
locally, Render's Environment settings in production). The refresh token must have
activity:read_all (or activity:read) access; get one with strava_connect.py.
Secrets are never logged.
"""

import logging
import os
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

API_URL = "https://www.strava.com/api/v3"
TOKEN_URL = "https://www.strava.com/oauth/token"
SKI_SPORTS = {"AlpineSki", "Snowboard"}  # lift-served days (not backcountry or Nordic)
STREAM_KEYS = "time,latlng,altitude,velocity_smooth"


@dataclass(frozen=True)
class Credentials:
    client_id: str
    client_secret: str
    refresh_token: str

    @classmethod
    def from_env(cls) -> "Credentials | None":
        values = [os.environ.get(k, "").strip() for k in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN")]
        return cls(*values) if all(values) else None


@dataclass(frozen=True)
class Track:
    """One activity's GPS track, point by point."""

    time: list[float]  # seconds from the start
    lat: list[float]
    lon: list[float]
    altitude: list[float]  # meters
    speed: list[float] | None  # meters per second, when Strava has it


class StravaError(RuntimeError):
    pass


class Strava:
    """Exchanges the refresh token for short-lived access tokens as needed."""

    def __init__(self, client: httpx.AsyncClient, credentials: Credentials) -> None:
        self.client = client
        self.credentials = credentials
        self._access_token: str | None = None
        self._expires_at = 0.0

    async def _token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        c = self.credentials
        response = await self.client.post(TOKEN_URL, data={
            "client_id": c.client_id, "client_secret": c.client_secret,
            "grant_type": "refresh_token", "refresh_token": c.refresh_token,
        })
        if not response.is_success:
            raise StravaError(f"Strava token refresh failed ({response.status_code})")
        body = response.json()
        if body["refresh_token"] != c.refresh_token:
            # Strava replaced the refresh token; the old one stops working. Keep the new one
            # for this process, and say so (without printing it) so the stored copy gets updated.
            log.warning("Strava issued a new refresh token; run strava_connect.py again or update STRAVA_REFRESH_TOKEN")
            self.credentials = Credentials(c.client_id, c.client_secret, body["refresh_token"])
        self._access_token, self._expires_at = body["access_token"], float(body["expires_at"])
        return self._access_token

    async def _get(self, path: str, **params) -> object:
        response = await self.client.get(f"{API_URL}{path}", params=params, headers={"Authorization": f"Bearer {await self._token()}"})
        if response.status_code == 401:
            raise StravaError("Strava refused access; the refresh token needs activity:read_all (run strava_connect.py)")
        if response.status_code == 429:
            raise StravaError("Strava rate limit reached; try again later")
        response.raise_for_status()
        return response.json()

    async def ski_activities(self, limit: int) -> list[dict]:
        """Newest ski and snowboard activities first, up to `limit`."""
        found, page = [], 1
        while len(found) < limit:
            batch = await self._get("/athlete/activities", per_page=100, page=page)
            found += [a for a in batch if a.get("sport_type") in SKI_SPORTS]
            if len(batch) < 100:
                break
            page += 1
        return found[:limit]

    async def track(self, activity_id: int) -> Track | None:
        """The activity's GPS track, or None if it has no GPS (e.g. recorded indoors)."""
        streams = await self._get(f"/activities/{activity_id}/streams", keys=STREAM_KEYS, key_by_type="true")
        if "latlng" not in streams or "altitude" not in streams:
            return None
        latlng = streams["latlng"]["data"]
        speed = streams.get("velocity_smooth", {}).get("data")
        return Track(
            time=[float(t) for t in streams["time"]["data"]],
            lat=[p[0] for p in latlng],
            lon=[p[1] for p in latlng],
            altitude=[float(a) for a in streams["altitude"]["data"]],
            speed=[float(s) for s in speed] if speed else None,
        )
