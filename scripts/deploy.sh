#!/usr/bin/env bash
# Runbook de primeiro deploy — Radar de Editais.
#
#   Backend : Railway  (2 serviços na MESMA imagem Dockerfile: web + worker)
#   Frontend: Vercel   (root = frontend/)
#   DB      : Supabase Cloud (Postgres + pgvector + Auth)
#
# Checklist GUIADO — não executa nada. Rode cada passo e verifique.
# build/restartPolicy ficam pinados em ./railway.json (builder=DOCKERFILE).
set -e
cat <<'EOF'
════════════════════════════════════════════════════════════════════════════
0) PRÉ-REQUISITOS (uma vez)
   - Projeto Supabase Cloud criado.
   - Projeto Railway criado (railway.app) + Railway CLI: npm i -g @railway/cli
   - Conta Vercel.
   - .env.cloud na raiz com os 6 valores do cloud (ver bloco ENV no fim) —
     usado pelos scripts populate_cloud_kg.py / smoke_cloud.py / reindex.

════════════════════════════════════════════════════════════════════════════
1) SUPABASE CLOUD — schema + dados
   a. Aplicar TODAS as migrations (supabase/migrations/001..018).
        supabase link --project-ref <ref>
        supabase db push
      (003 = schema do procrastinate → habilita o worker;
       016 = kg_artifacts → KG vive no Postgres, não em arquivo.)
   b. Popular catálogo + KG no cloud:
        .venv/bin/python scripts/populate_cloud_kg.py
   c. Validar tudo:
        .venv/bin/python scripts/smoke_cloud.py     # 5 checks → 5 PASS

════════════════════════════════════════════════════════════════════════════
2) RAILWAY — serviço WEB (FastAPI)
   a. railway login && railway link        # vincula ao projeto
   b. New Service → Deploy from Repo. Railway lê ./railway.json → Dockerfile.
      O CMD do Dockerfile (uvicorn --port ${PORT}) já é o start command do web.
   c. Setar variáveis (ver bloco ENV) — secrets + config + FRONTEND_URL + RERANK.
        railway variables --set "OPENAI_API_KEY=..." --set "KG_STORE_BACKEND=postgres" ...
   d. Settings → Deploy → Healthcheck Path = /   (SÓ no web; / retorna dict leve.)
   e. Deploy. Anote a URL pública:  https://<web>.up.railway.app

════════════════════════════════════════════════════════════════════════════
3) RAILWAY — serviço WORKER (procrastinate)  ← fácil de esquecer
   Sem ele: chunk_edital / enrich_content (upload da library) / cron de
   discovery (04:00 UTC) NUNCA rodam.
   a. + New Service → MESMO repo (reusa a imagem).
   b. Settings → Deploy → Custom Start Command:
        python -m procrastinate --app=core.tasks.app worker
   c. Mesmas SECRETS + CONFIG do web (NÃO precisa FRONTEND_URL/RERANK).
   d. NÃO setar Healthcheck Path (worker não tem HTTP → falharia o check).
   e. Garantir que o serviço fica SEMPRE ACORDADO (não habilitar sleep/scale-to-zero;
      o cron e a fila dependem do worker vivo).

════════════════════════════════════════════════════════════════════════════
4) VERCEL — frontend
   a. Import repo → Root Directory = frontend/
   b. Env: NEXT_PUBLIC_API_URL = https://<web>.up.railway.app
   c. Deploy. Anote o domínio: https://<app>.vercel.app

════════════════════════════════════════════════════════════════════════════
5) FECHAR O LOOP CORS  ← sem isto o frontend não fala com o backend
   No serviço WEB do Railway:
        railway variables --set "FRONTEND_URL=https://<app>.vercel.app"
   (CSV se houver mais de uma origem). Redeploy o web.

════════════════════════════════════════════════════════════════════════════
6) PÓS-DEPLOY
   a. Reindex com Contextual Retrieval (hoje só finep:779 contextualizado):
        .venv/bin/python scripts/reindex_edital.py --all --force   # contra o cloud
   b. Smoke final + abrir o frontend e fazer um turno de escrita real.

────────────────────────────────────────────────────────────────────────────
ENV — referência (Railway dashboard/CLI; NÃO versionar valores)
  SECRETS         (web + worker):
     OPENAI_API_KEY  DATABASE_URL(pooler Supabase)  SUPABASE_URL
     SUPABASE_ANON_KEY  SUPABASE_SERVICE_KEY  SUPABASE_JWT_SECRET
  CONFIG          (web + worker):
     LLM_BACKEND=openai   OPENAI_MODEL=gpt-4o-mini   KG_STORE_BACKEND=postgres
     EMBEDDING_MODEL=text-embedding-3-small   RETRIEVAL_EMBEDDING_COLUMN=embedding
  CONFIG          (web only):
     FRONTEND_URL=<vercel>          # CORS
     RERANK_BACKEND=off             # RRF puro p/ beta (~400ms). cross-encoder NÃO
                                    # está na imagem (extra [rerank]); =llm rerankeia
                                    # via API mas custa ~3s/retrieval.
  MONITORING      (web + worker — código pronto em telemetry.py/logging_config.py; só setar):
     LANGFUSE_PUBLIC_KEY  LANGFUSE_SECRET_KEY   # traces LLM + custo (cloud.langfuse.com, free tier)
     SENTRY_DSN  ENV=production                 # error tracking (sentry.io, free tier)
     LOG_FORMAT=json                            # logs estruturados p/ o coletor do Railway
  OPCIONAIS:
     TAVILY_API_KEY                 # discovery/deep_research (degrada sem)
     CONTEXTUAL_RETRIEVAL=true      # default; relevante no reindex (passo 6a)
════════════════════════════════════════════════════════════════════════════
EOF
