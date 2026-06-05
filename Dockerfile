# Dockerfile for Radar de Editais backend (Fly.io)
# The web process uses the CMD below (uvicorn).
# The worker process overrides this CMD via fly.toml [processes.worker].
# See ADR-001-decisoes-iniciais.md (D1, D3, D4).

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY config.py ./
COPY backend/ ./backend/
COPY core/ ./core/
COPY domain/ ./domain/
COPY pipeline/ ./pipeline/
COPY scripts/ ./scripts/
COPY wikis/ ./wikis/
COPY WIKI.md ./

RUN pip install --no-cache-dir -e .

RUN useradd -m -u 1000 app && chown -R app:app /app
USER app

EXPOSE 8000

# Shell-form para expandir ${PORT}: Railway injeta uma porta dinâmica; Fly/local
# caem no fallback 8000 (internal_port do fly.toml). O worker sobrescreve este CMD.
CMD uvicorn backend.api:app --host 0.0.0.0 --port ${PORT:-8000}
