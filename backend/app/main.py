import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="API")

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


# In production the built Angular app is served from here. Mounted last so /api routes win.
static_dir = Path(os.environ.get("STATIC_DIR", Path(__file__).parents[2] / "frontend/dist/frontend/browser"))
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
