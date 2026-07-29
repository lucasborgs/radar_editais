# Operação dos CRONs P0

`cron_runs` é a unidade autoritativa de `run_daily_etl`,
`discover_opportunities` e `warm_edital_chunks`; `source_runs` continua sendo
o detalhe por canal. O painel read-only é `GET /admin/cron-operations` e exige
operador administrativo.

`python scripts/cron_ops.py list` lista somente id, tarefa, estado, tentativas
e horários. Nunca imprime `args`. O comando é dry-run por padrão.

Recuperação: exportar snapshot somente leitura; confirmar que um `doing` acima
de `CRON_STUCK_MINUTES` não tem worker vivo; encerrar apenas ids órfãos com
`python scripts/cron_ops.py finish-stuck --job-id ID --apply`; reenfileirar
falhas recuperáveis com `retry-failed --job-id ID --apply`; executar o cron
correspondente e conferir `cron_runs`, `source_runs` e artefatos produzidos.
Estas mutações não foram executadas neste trabalho. Não remover histórico.

O dead-man deve rodar fora do worker observado. `operational_incidents` deduplica
alertas e registra recuperação. Para testar SMTP, use temporariamente uma caixa
de teste e `ALERT_EMAIL_TO`, valide recebimento e remova a configuração; nunca
inclua perfil, CNPJ, prompt, conteúdo ou traceback.

## Dead-man externo

Agende `python scripts/cron_deadman.py` a cada 30 minutos no scheduler do
provedor, com `DATABASE_URL` e as variáveis SMTP já usadas pelo canal de alerta.
Ele não importa `radar.core.tasks`, não lê `args` e consulta apenas os ledgers,
jobs e heartbeats. Ações de alerta são deduplicadas por incidente; quando o
heartbeat ou o CRON volta, a mesma rotina registra recuperação e envia uma
mensagem curta. Se a rotina falhar, confira conectividade do banco, credencial
SMTP e `operational_incidents`; nunca rode com `DEMO_MODE` em produção.

Rollback da migration local/staging: restaurar o snapshot do banco ou aplicar
uma migration reversa revisada pelo operador. A migration 047 não deve ser
removida manualmente em produção, pois o ledger é histórico operacional.
