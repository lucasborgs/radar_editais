# Dockerfile for Radar de Editais backend.
# Imagem única para os 2 serviços do docker-compose.yml:
#   app    → usa o CMD abaixo (uvicorn).
#   worker → sobrescreve o CMD no docker-compose.yml
#            (python -m procrastinate --app=core.tasks.app worker).
# See ADR-001-decisoes-iniciais.md (D1, D3, D4).

FROM python:3.11-slim AS base

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

FROM base AS app

RUN useradd -m -u 1000 app && chown -R app:app /app
USER app

EXPOSE 8000

# Shell-form para expandir ${PORT}, caso o ambiente injete uma porta dinâmica;
# local cai no fallback 8000. O serviço worker sobrescreve este CMD (ver cabeçalho).
CMD uvicorn backend.api:app --host 0.0.0.0 --port ${PORT:-8000}

# O Crawl4AI é uma capacidade opcional da Descoberta. Fica exclusivamente no
# worker para não aumentar a imagem nem a superfície da API síncrona.
FROM base AS worker

RUN pip install --no-cache-dir crawl4ai \
    && python -m playwright install --with-deps chromium \
    && useradd -m -u 1000 app \
    && chown -R app:app /app
USER app

CMD python -m procrastinate --app=core.tasks.app worker
