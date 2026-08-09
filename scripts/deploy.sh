#!/usr/bin/env bash
# Runbook de deploy — Radar de Editais.
#
#   Backend : Docker Compose (app + worker procrastinate), mesma imagem Dockerfile
#   Tunnel  : Cloudflare Tunnel (cloudflared) — expõe app:8000 num domínio próprio
#   Frontend: Vercel   (root = frontend/)
#   DB      : Supabase Cloud (Postgres + pgvector + Auth)
#
# Checklist GUIADO — não executa nada. Rode cada passo e verifique.
set -e
cat <<'EOF'
════════════════════════════════════════════════════════════════════════════
0) PRÉ-REQUISITOS (uma vez)
   - Docker + Docker Compose instalados no host que vai rodar o backend.
   - Projeto Supabase Cloud criado.
   - Conta Cloudflare com domínio no DNS + `cloudflared tunnel create <nome>`
     rodado uma vez (gera credentials.json + tunnel UUID).
   - Conta Vercel.
   - .env na raiz com as env vars de produção (ver bloco ENV no fim).

════════════════════════════════════════════════════════════════════════════
1) SUPABASE CLOUD — schema
   a. Aplicar TODAS as migrations versionadas em supabase/migrations/:
        ENVIRONMENT=production ALLOW_ENVIRONMENT_INITIALIZATION=1 \
        ALLOW_PRODUCTION_MUTATION=1 CONFIRM_PROJECT_REF=<ref> \
        python scripts/supabase_safe.py push
      (003 = schema do procrastinate → habilita o worker;
       035 = match_verdicts; 036 = catálogo gold relacional;
       037 remove company_hypergraphs; 038 = estado da promoção da Descoberta.)
   b. O catálogo gold é populado pelo cron diário do worker
      (run_daily_etl, 03:00 UTC) via core.kg.gold.ingest_all().

════════════════════════════════════════════════════════════════════════════
2) CLOUDFLARE TUNNEL — cloudflared/config.yml (gitignored, credenciais sensíveis)
   a. cloudflared tunnel create radar-editais   # se ainda não existe
      → gera cloudflared/credentials.json + UUID do tunnel.
   b. Crie cloudflared/config.yml:
        tunnel: <uuid-do-tunnel>
        credentials-file: /etc/cloudflared/credentials.json
        ingress:
          - hostname: api.seudominio.com.br
            service: http://app:8000
          - service: http_status:404
   c. DNS: cloudflared tunnel route dns radar-editais api.seudominio.com.br

════════════════════════════════════════════════════════════════════════════
3) DOCKER COMPOSE — app + worker + tunnel (docker-compose.yml)
   a. .env na raiz com as env vars de produção (ver bloco ENV no fim).
   b. Gere uma tag imutável a partir do commit e suba a release:
        export IMAGE_TAG="$(git rev-parse --short HEAD)"
        scripts/compose.sh production build app worker
        scripts/compose.sh production up -d
      Sobe 3 serviços: app (uvicorn, porta 8000), worker (procrastinate,
      sem HTTP) e tunnel (cloudflared, lê cloudflared/config.yml). App e worker
      ficam retidos como imagens separadas com a mesma tag de commit.
   c. Verificar:
        scripts/compose.sh production ps                 # os 3 "Up"
        scripts/compose.sh production logs -f app        # sem erro no boot
        curl --fail https://api.seudominio.com.br/health  # API + Postgres
   d. O worker é quem roda chunk_edital / enrich_content / os crons de
      scrape+discovery (03:00/04:00 UTC) — sem ele nada disso roda.
   e. Rollback (as imagens da tag anterior precisam continuar no host):
        docker image ls 'radar-editais-*'   # escolha o SHA anterior validado
        export IMAGE_TAG=<sha-anterior>
        scripts/compose.sh production up -d --no-build app worker
        curl --fail https://api.seudominio.com.br/health
      Se o healthcheck falhar, restaure `IMAGE_TAG` para a tag que estava ativa
      e repita o `up --no-build`. Não rode `docker image prune` antes de encerrar
      a janela de rollback.

════════════════════════════════════════════════════════════════════════════
4) VERCEL — frontend
   a. Import repo → Root Directory = frontend/
   b. Env: NEXT_PUBLIC_API_URL = https://api.seudominio.com.br
   c. Deploy. Anote o domínio: https://<app>.vercel.app

════════════════════════════════════════════════════════════════════════════
5) FECHAR O LOOP CORS  ← sem isto o frontend não fala com o backend
   No .env do Docker Compose (host):
        FRONTEND_URL=https://<app>.vercel.app
   (CSV se houver mais de uma origem). Preserve `IMAGE_TAG` e rode
   `scripts/compose.sh production up -d --build app`.

════════════════════════════════════════════════════════════════════════════
6) PÓS-DEPLOY
   a. Reindex com Contextual Retrieval se precisar forçar re-chunking:
        scripts/compose.sh production exec app python scripts/reindex_edital.py --all --force
   b. Abrir o frontend e fazer um turno de escrita real (smoke manual).

────────────────────────────────────────────────────────────────────────────
ENV — referência (.env na raiz, lido por app+worker via env_file no compose)
  SECRETS         (app + worker):
     OPENAI_API_KEY  DATABASE_URL(pooler Supabase)  SUPABASE_URL
     SUPABASE_ANON_KEY  SUPABASE_SERVICE_KEY  SUPABASE_JWT_SECRET
  CONFIG          (app + worker):
     ENVIRONMENT=production
     KG_STORE_BACKEND=postgres
     LLM_BACKEND=openai   OPENAI_MODEL=gpt-4o-mini
     EMBEDDING_MODEL=text-embedding-3-small   RETRIEVAL_EMBEDDING_COLUMN=embedding
  CONFIG          (app only):
     FRONTEND_URL=<vercel>          # CORS
     RERANK_BACKEND=off             # RRF puro p/ beta (~400ms). cross-encoder NÃO
                                    # está na imagem (extra [rerank]); =llm rerankeia
                                    # via API mas custa ~3s/retrieval.
  MONITORING      (app + worker — código pronto em telemetry.py/logging_config.py; só setar):
     LANGFUSE_PUBLIC_KEY  LANGFUSE_SECRET_KEY   # traces LLM + custo (cloud.langfuse.com, free tier)
     SENTRY_DSN  ENV=production                 # error tracking (sentry.io, free tier)
     LOG_FORMAT=json                            # logs estruturados
  OPCIONAIS:
     TAVILY_API_KEY                 # discovery/deep_research (degrada sem)
     CONTEXTUAL_RETRIEVAL=true      # default; relevante no reindex (passo 6a)
════════════════════════════════════════════════════════════════════════════
EOF
