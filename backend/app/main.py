import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.quotes import configured_symbols, router as quotes_router, run_market_data_on_demand
from app.lifts import router as lifts_router, run_my_laps, run_routes
from app.snow import report as snow_report, router as snow_router

# Local secrets (e.g. Strava) from backend/.env; on Render they come from the environment.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Little runs in the background, to keep CPU and requests to other sites down:
    # - exchange quotes stream only while someone has the markets page open (MARKET_DATA_ENABLED=0: never);
    # - snow totals and Lift Blog data are fetched when asked for and stale, and kept in the database
    #   (SNOW_ENABLED=0 stops snow fetching);
    # - Strava is checked daily and the lift maps monthly (LIFTS_ENABLED=0 turns both off).
    tasks = []
    if os.environ.get("MARKET_DATA_ENABLED", "1") != "0":
        tasks.append(asyncio.create_task(run_market_data_on_demand(configured_symbols())))
    snow_report.enabled = os.environ.get("SNOW_ENABLED", "1") != "0"
    if os.environ.get("LIFTS_ENABLED", "1") != "0":
        tasks.append(asyncio.create_task(run_my_laps()))  # Strava does nothing without STRAVA_ variables
        tasks.append(asyncio.create_task(run_routes()))
    yield
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="API", lifespan=lifespan)

# Allow the Angular dev server to call the API directly (the dev proxy also covers this).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(quotes_router)
app.include_router(snow_router)
app.include_router(lifts_router)


class SPAStaticFiles(StaticFiles):
    """Serves the Angular app; unknown paths like /markets get index.html so the router can handle them.

    index.html is sent with Cache-Control: no-cache so browsers check for a new version
    after each deploy (otherwise a cached copy can point to scripts that no longer exist).
    The scripts themselves have content hashes in their names, so they can be cached.
    """

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as error:
            # Missing files (e.g. an old main-ABC123.js) stay 404s; only page routes get the app.
            if error.status_code != 404 or scope["path"].startswith("/api/") or Path(path).suffix:
                raise
            response = await super().get_response("index.html", scope)
        if response.media_type == "text/html":
            response.headers["Cache-Control"] = "no-cache"
        return response


# In production the built Angular app is served from here. Mounted last so /api routes win.
static_dir = Path(os.environ.get("STATIC_DIR", Path(__file__).parents[2] / "frontend/dist/frontend/browser"))
if static_dir.is_dir():
    app.mount("/", SPAStaticFiles(directory=static_dir, html=True), name="frontend")
