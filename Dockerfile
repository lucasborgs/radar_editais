# Dockerfile for Radar de Editais backend (Railway).
# Imagem única para 2 serviços Railway:
#   web    → usa o CMD abaixo (uvicorn).
#   worker → sobrescreve o CMD via Custom Start Command no Railway
#            (python -m procrastinate --app=core.tasks.app worker). Ver scripts/deploy.sh.
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

# Shell-form para expandir ${PORT}: Railway injeta uma porta dinâmica; local cai
# no fallback 8000. O serviço worker sobrescreve este CMD (ver cabeçalho).
CMD uvicorn backend.api:app --host 0.0.0.0 --port ${PORT:-8000}
