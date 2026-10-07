# david_cornes

FastAPI backend + Angular frontend.

```
backend/    FastAPI app (Python 3.12)
frontend/   Angular 22 app (Node 22+)
```

## Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
fastapi dev app/main.py
```

API runs at http://localhost:8000 — interactive docs at http://localhost:8000/docs.

Run tests:

```bash
pytest
```

## Frontend

```bash
cd frontend
npm install
npm start
```

App runs at http://localhost:4200; `/api/*` requests are proxied to the backend on port 8000 (see `frontend/proxy.conf.json`). Start the backend first.

Run tests / build:

```bash
npm test
npm run build
```
