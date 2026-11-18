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

from app.quotes import configured_symbols, router as quotes_router, run_market_data
from app.lifts import router as lifts_router, run_lifts, run_my_laps, run_routes
from app.snow import router as snow_router, run_snow

# Local secrets (e.g. Strava) from backend/.env; on Render they come from the environment.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Stream exchange quotes in the background for the whole life of the server.
    # Set MARKET_DATA_ENABLED=0 to turn it off (e.g. when working only on the résumé).
    # Bridger Bowl snow totals are fetched hourly (SNOW_ENABLED=0 turns that off), and
    # lift data daily (LIFTS_ENABLED=0).
    tasks = []
    if os.environ.get("MARKET_DATA_ENABLED", "1") != "0":
        tasks.append(asyncio.create_task(run_market_data(configured_symbols())))
    if os.environ.get("SNOW_ENABLED", "1") != "0":
        tasks.append(asyncio.create_task(run_snow()))
    if os.environ.get("LIFTS_ENABLED", "1") != "0":
        tasks.append(asyncio.create_task(run_lifts()))
        tasks.append(asyncio.create_task(run_my_laps()))  # does nothing without STRAVA_ variables
        tasks.append(asyncio.create_task(run_routes()))  # lift and run maps for the route map, weekly
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
