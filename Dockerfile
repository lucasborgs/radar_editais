# Dockerfile for Radar de Editais backend.
# Imagem única para os 2 serviços do docker-compose.yml:
#   app    → usa o CMD abaixo (uvicorn).
#   worker → sobrescreve o CMD no docker-compose.yml
#            (python -m procrastinate --app=radar.core.tasks.app worker).
# See docs/historical/ADR-001-decisoes-iniciais.md (D1, D3, D4).

FROM python:3.12-slim AS package-builder

WORKDIR /build

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

FROM python:3.12-slim AS base

WORKDIR /app

COPY --from=package-builder /wheels/ /wheels/

# O runtime carrega o código de /app/src para preservar os paths de dados do
# projeto. O wheel instalado fornece metadata/entry points sem levar a
# toolchain de build (setuptools/wheel/compilador) para a imagem final.
ENV PYTHONPATH=/app/src

COPY pyproject.toml requirements.lock.txt requirements.worker.lock.txt ./
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY wikis/ ./wikis/
COPY WIKI.md ./
COPY skills/ ./skills/

RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt \
    && pip install --no-cache-dir --no-deps /wheels/*.whl \
    && rm -rf /wheels \
    && python -c "from radar.core.skills import load_playbook; assert load_playbook('subvencao', 'finep', include_overlays=False).sections"

FROM base AS app

RUN useradd -m -u 1000 app && chown -R app:app /app
USER app

EXPOSE 8000

# Shell-form para expandir ${PORT}, caso o ambiente injete uma porta dinâmica;
# local cai no fallback 8000. O serviço worker sobrescreve este CMD (ver cabeçalho).
CMD uvicorn radar.api.app:app --host 0.0.0.0 --port ${PORT:-8000}

# O Crawl4AI é uma capacidade opcional da Descoberta. Fica exclusivamente no
# worker para não aumentar a imagem nem a superfície da API síncrona.
FROM base AS worker

# Compartilhado entre a instalação (root) e o processo do worker (app).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN pip install --no-cache-dir --require-hashes -r requirements.worker.lock.txt \
    && python -m playwright install --with-deps chromium \
    && useradd -m -u 1000 app \
    && chown -R app:app /app /ms-playwright
USER app

CMD python -m procrastinate --app=radar.core.tasks.app worker
