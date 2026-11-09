# --- Build the Angular app ---
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Run FastAPI, serving the API and the built frontend ---
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATIC_DIR=/app/static

COPY backend/requirements.txt ./
# The live markets page uses the collector's market_data package (exchange streams + MarketDataHandler).
# Only its code is copied; collector/.env never enters the image (see .dockerignore).
COPY collector/pyproject.toml /collector/pyproject.toml
COPY collector/market_data /collector/market_data
RUN pip install --no-cache-dir -r requirements.txt /collector

COPY backend/app ./app
COPY --from=frontend /frontend/dist/frontend/browser ./static

# Render provides PORT; default to 8000 for local runs.
EXPOSE 8000
CMD ["sh", "-c", "fastapi run app/main.py --host 0.0.0.0 --port ${PORT:-8000}"]
