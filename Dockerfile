# syntax=docker/dockerfile:1
# FoodSupply ERP — single-service build.
# Replaces the previous per-service Dockerfiles (backend/Dockerfile, frontend/Dockerfile).
# Stage 1 builds the React frontend; stage 2 runs FastAPI which serves both
# the API and the compiled SPA on the same origin (no nginx / CORS needed).

# ---------- Stage 1: build the React frontend ----------
FROM node:22-alpine AS frontend-build
WORKDIR /frontend

# Install npm deps with a separate layer for better caching.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund

# Build-time env for the WeCom gateway URL (consumed by Vite at build time).
# Set VITE_WECOM_GATEWAY_URL as a Railway build variable; empty default is OK
# for the build itself (the runtime gateway reachability is a separate concern).
ARG VITE_WECOM_GATEWAY_URL=""
ENV VITE_WECOM_GATEWAY_URL=$VITE_WECOM_GATEWAY_URL

COPY frontend/ ./
RUN npm run build
# Output: /frontend/dist

# ---------- Stage 2: runtime — FastAPI serving API + SPA ----------
FROM python:3.13-slim
WORKDIR /app

# Install Python dependencies first (layer cache).
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code (the `app` package and the top-level `seed.py` module).
# .dockerignore strips tests/, data/, erp.db, etc.
COPY backend/app ./app
COPY backend/seed.py ./seed.py

# Copy the built frontend into /app/static (served by FastAPI).
COPY --from=frontend-build /frontend/dist ./static

# Ensure /app is on the import path so `from seed import seed` resolves.
ENV PYTHONPATH=/app

EXPOSE 8000
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
