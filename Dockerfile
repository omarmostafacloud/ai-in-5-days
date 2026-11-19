# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# Non-root runtime user
RUN useradd --create-home --uid 10001 den

COPY pyproject.toml README.md ./
COPY dragons_den ./dragons_den
COPY main.py ./
RUN pip install --upgrade pip && pip install .

USER den
EXPOSE 8080

# No secrets are baked into the image: GOOGLE_API_KEY etc. are injected by Cloud Run
# from Secret Manager at runtime (see terraform/cloud_run.tf).
CMD ["python", "main.py"]
