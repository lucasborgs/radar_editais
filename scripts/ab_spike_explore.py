"""A/B do ExploreAgent com e sem as tools de grafo do spike (Trilha 1).

Reproduz a Conversa 1 (produção, perfil completo agro+deep tech) nos DOIS
modos, no MESMO processo, com o MESMO perfil — eliminando os confundidores do
teste manual de 2026-07-31 (perfis diferentes, LLM não determinístico).

Motivo de ser standalone (e NÃO uma suite do harness): o harness de eval
bloqueia ambientes de produção (fail-closed, `_refuse_hostile_environment`),
e este diagnóstico precisa rodar CONTRA a base de produção onde o `kg_spike`
vive. Reusa `ExploreAgent.explore_with_meta` diretamente (mesmo padrão de
`scripts/export_to_obsidian_spike.py`).

Uso:
    DATABASE_URL=... ALLOW_PRODUCTION_MUTATION=1 \
        python scripts/ab_spike_explore.py
    python scripts/ab_spike_explore.py --question 3   # só a pergunta 3 (debug)
    python scripts/ab_spike_explore.py --no-match      # sem as match tools (isola o grafo)

Saída: `eval_results/spike_ab_<ts>.json` (respostas + `called_tools` por lado)
e, em stdout, um resumo lado-a-lado com a lista de tools chamadas.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from radar.core.config import ROOT

EVAL_OUT_DIR = ROOT / "eval_results"
EVAL_OUT_DIR.mkdir(parents=True, exist_ok=True)

# Perfil fixo — reproduz a Conversa 1 de produção (agro + deep tech, TRL 7,
# porte médio, UF SC). Inclui `setores`/`tema` (lidos por graph_reason) e os
# campos textuais (lidos por infer_company_setores nas match tools).
PROFILE: dict = {
    "nome": "AgriDeep Tech",
    "tipo_entidade": "startup",
    "one_liner": "Sensores de solo com visão computacional para agricultura de precisão.",
    "solution_summary": (
        "Desenvolvemos sensores IoT de baixo custo com ML onboard (visão computacional "
        "e espectroscopia NIR) para monitoramento contínuo de solo e cultura em tempo real."
    ),
    "descricao_atividades": (
        "Plataforma de agricultura de precisão: coleta de dados de solo/planta, análise "
        "preditiva de produtividade e recomendação agronômica para fazendas de soja e milho."
    ),
    "tamanho_empresa": "MEDIO",
    "uf": "SC",
    "trl": 7,
    "ano_fundacao": 2019,
    "estagio": "seed",
    "setores": ["agro", "agricultura"],
    "tema": ["agritech", "IA"],
    "tipos_financiamento_interesse": ["subvencao_nao_reembolsavel"],
}

# As 8 perguntas da Conversa 1 (produção).
QUESTIONS: list[dict] = [
    {"message": "Quais editais têm relação com o que minha empresa faz?", "node_id": None},
    {"message": "Que ICTs estão conectadas a esses editais?", "node_id": None},
    {"message": "Essas ICTs estão credenciadas por alguma agência?", "node_id": None},
    {"message": "Que setores e tecnologias tocam esses editais?", "node_id": None},
    {"message": "Quais editais são similares entre si?", "node_id": None},
    {"message": "Que comunidades existem neste catálogo?", "node_id": None},
    {"message": "Que programas de subvenção servem para mim?", "node_id": None},
    {"message": "Que investidores têm tese alinhada ao meu perfil?", "node_id": None},
]

# Rótulo para cada pergunta no resumo lado-a-lado (P1..P8).
QUESTION_LABELS = [f"P{i + 1}" for i in range(len(QUESTIONS))]

# Âncora da conversa: o agente abre com um edital em foco (nó de contexto).
HINT_TARGET = {"edital": "edital:finep:783"}


def _run_side(
    label: str,
    flag: str | None,
    questions: list[tuple[int, dict]],
    with_match: bool,
) -> list[dict]:
    from radar.core.services.explore_agent import ExploreAgent

    os.environ.pop("KG_SPIKE_ENABLED", None)
    if flag is not None:
        os.environ["KG_SPIKE_ENABLED"] = flag

    agent = ExploreAgent()
    results: list[dict] = []
    for idx, q in questions:
        try:
            answer, meta = agent.explore_with_meta(
                q["message"],
                history=None,
                edital_ids=None,
                node_id=HINT_TARGET["edital"],
                node_type="edital",
                has_profile=True,
                profile_text=(
                    f"Empresa: {PROFILE['nome']} | Proposta: {PROFILE['one_liner']} "
                    f"| Porte: {PROFILE['tamanho_empresa']} | UF: {PROFILE['uf']} "
                    f"| TRL: {PROFILE['trl']} | Estágio: {PROFILE['estagio']}"
                ),
                profile=PROFILE if with_match else None,
            )
            results.append({
                "idx": idx,
                "label": QUESTION_LABELS[idx],
                "question": q["message"],
                "answer": answer,
                "called_tools": meta.get("called_tools", []),
                "stop_reason": meta.get("stop_reason"),
            })
        except Exception as e:  # noqa: BLE001
            results.append({
                "idx": idx,
                "label": QUESTION_LABELS[idx],
                "question": q["message"],
                "answer": f"ERRO: {e}",
                "called_tools": [],
                "stop_reason": "error",
            })
        print(f"  [{label}] {QUESTION_LABELS[idx]}: {q['message'][:60]!r} … ok")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", type=int, default=None, help="Só a pergunta N (1-8)")
    parser.add_argument("--no-match", action="store_true", help="Não injetar as match tools (isola o grafo)")
    args = parser.parse_args()

    from radar.core.environment import load_environment_profile

    load_environment_profile()

    questions = QUESTIONS
    if args.question is not None:
        idx = args.question - 1
        if idx < 0 or idx >= len(QUESTIONS):
            parser.error(f"--question deve ser 1-{len(QUESTIONS)}")
        questions = [QUESTIONS[idx]]
    numbered = list(enumerate(questions))

    print("→ rodando lado A (OFF, gold-only)…")
    side_a = _run_side("A-off", flag=None, questions=numbered, with_match=not args.no_match)
    print("→ rodando lado B (ON, spike graph)…")
    side_b = _run_side("B-on", flag="1", questions=numbered, with_match=not args.no_match)

    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "profile": PROFILE,
        "with_match": not args.no_match,
        "sides": {"off_gold": side_a, "on_spike": side_b},
    }
    out = EVAL_OUT_DIR / f"spike_ab_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✓ resultado gravado: {out}")
    print("\n" + "=" * 78)
    for a, b in zip(side_a, side_b, strict=True):
        print(f"\n── {a['label']} ── {a['question'][:80]}")
        print(f"  OFF tools: {a['called_tools']}")
        print(f"  ON  tools: {b['called_tools']}")


if __name__ == "__main__":
    main()
