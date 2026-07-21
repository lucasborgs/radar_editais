"""
Avaliação INTRÍNSECA de parsing/chunking (estágio 1 do bake-off, docs/domain/schema.md §11/§12).

Métricas baratas, sem golden e sem juiz LLM, para filtrar estratégias de
parsing/chunking ANTES do end-to-end (estágio 2, suíte `rag`). Medem a
qualidade estrutural do segmento/chunk em si:

  • distribuição de tamanho (p50/p95/max)
  • % oversize (acima do teto) e % órfão (abaixo do piso) — a patologia de
    "unit gigante" e "micro-chunk órfão" que motivou a frente
  • alinhamento a fronteira de seção (quando há section_path)

Inspirado na metodologia token-level da Chroma ("Evaluating Chunking
Strategies for Retrieval"): a versão token-IoU contra spans-gold entra no
estágio 1b (precisa de golden de spans). Aqui ficam as métricas sem-gold.

Funções puras: sem I/O, sem LLM.
"""
from __future__ import annotations

from statistics import median


def size_distribution(
    texts: list[str],
    *,
    length=len,
    oversize_threshold: int,
    orphan_threshold: int,
) -> dict:
    """Distribuição de tamanho de uma lista de segmentos/chunks.

    `length`: função de tamanho (chars=`len` default; tokens via uma contagem).
    `oversize_threshold`/`orphan_threshold`: tetos/pisos para as taxas de
    patologia. Retorna dict JSON-serializable.
    """
    if not texts:
        return {"n": 0, "p50": 0, "p95": 0, "max": 0,
                "pct_oversize": 0.0, "pct_orphan": 0.0}
    sizes = sorted(length(t) for t in texts)
    n = len(sizes)
    p95 = sizes[min(n - 1, int(round(0.95 * (n - 1))))]
    oversize = sum(1 for s in sizes if s > oversize_threshold)
    orphan = sum(1 for s in sizes if s < orphan_threshold)
    return {
        "n": n,
        "p50": int(median(sizes)),
        "p95": int(p95),
        "max": sizes[-1],
        "pct_oversize": round(oversize / n, 4),
        "pct_orphan": round(orphan / n, 4),
    }


def section_coverage(chunks: list[dict]) -> dict:
    """Quão bem os chunks carregam estrutura de seção.

    `chunks`: dicts no contrato do chunker (`{text, section, ...}`).
    Mede a fração com `section` não-vazia (sinal de que o parser preservou a
    hierarquia) e quantas seções distintas foram cobertas.
    """
    if not chunks:
        return {"n": 0, "pct_com_secao": 0.0, "n_secoes_distintas": 0}
    com_secao = sum(1 for c in chunks if (c.get("section") or "").strip())
    secoes = {(c.get("section") or "").strip() for c in chunks if (c.get("section") or "").strip()}
    return {
        "n": len(chunks),
        "pct_com_secao": round(com_secao / len(chunks), 4),
        "n_secoes_distintas": len(secoes),
    }


def boundary_alignment(units: list[str], header_re) -> dict:
    """Alinhamento a fronteira: cabeçalhos (match de `header_re`) que iniciam
    uma unit OU vêm após `\\n\\n` (fronteira limpa) vs cabeçalhos "colados" no
    meio de um parágrafo (sinal de corte cego).

    `header_re`: regex compilado que casa o início de um cabeçalho.
    Retorna a fração de cabeçalhos em fronteira limpa (1.0 = parser perfeito).
    """
    total = clean = 0
    for u in units:
        for m in header_re.finditer(u):
            i = m.start()
            total += 1
            if i == 0 or u[max(0, i - 2):i] == "\n\n":
                clean += 1
    return {
        "n_headers": total,
        "pct_em_fronteira": round(clean / total, 4) if total else 1.0,
    }
