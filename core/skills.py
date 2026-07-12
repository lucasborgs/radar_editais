"""
Playbook loader — compõe a COMPETÊNCIA de escrita por mecanismo (+ overlay de fonte).

Modelo: a skill NÃO é conhecimento, é
competência. Regra dura do edital vem do RAG; aqui mora o craft tácito — keyed por
`mechanism` (instrumento), com overlays de praxe por `source` (agência).

Resolução em 3 camadas, merge POR SEÇÃO (cada `## titulo` é um tipo que roteia para
um consumidor):

    playbook = mechanism/<mech>.md          (base reusável)
             + source/<source>/global.md    (praxe da agência, todo mecanismo)
             + source/<source>/<mech>.md     (praxe agência × mecanismo)

Convenções dos arquivos de playbook:
  • `# Título` (H1) é ignorado; o comentário `<!-- SEED ... -->` e os comentários
    inline `<!-- → consumidor -->` nos cabeçalhos são removidos.
  • A prosa entre o H1 e o primeiro `##` é a **Lente** (vai ao Redator).
  • Uma linha `---` encerra o conteúdo roteável: tudo abaixo (o rodapé fato↔craft)
    é meta de autoria e NÃO entra em nenhum prompt.
  • `PLACEHOLDER` no corpo → arquivo tratado como ausente (scaffolding).

Roteamento:
  • Redator (geração)          → Lente + Padrões de escrita e tom + Praxe da agência
  • ComplianceMonitor (avalia) → Heurísticas de aprovação + Anti-padrões + Praxe da agência
  • Critic                     → NÃO recebe playbook (contrato estreito: só contradição).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# ── Seções canônicas (nome do `##` → tipo → consumidor) ─────────────────────
SECTION_LENTE = "Lente"
SECTION_WRITING = "Padrões de escrita e tom"
SECTION_HEURISTICS = "Heurísticas de aprovação"
SECTION_ANTIPATTERNS = "Anti-padrões / red flags"
SECTION_PRAXE = "Praxe da agência"

# Praxe da agência é contexto tonal/estratégico útil aos dois consumidores.
_WRITER_ORDER = (SECTION_WRITING, SECTION_PRAXE)
_MONITOR_ORDER = (SECTION_HEURISTICS, SECTION_ANTIPATTERNS, SECTION_PRAXE)

_LOW_CONF_MARKER = (
    "CONFIANÇA: BAIXA — mecanismo não identificado; orientação genérica, "
    "não específica do instrumento."
)

# ── Vocabulário canônico de mecanismo + sinônimos ──
# KG v2 PR2: fix da colisão — "investimento" mapeava para `credito`, mas o display
# de `equity` era "Investimento" (dois conceitos, um rótulo). Agora "investimento"→
# `equity` e o display de equity é "Equity/Investimento" (ver MECHANISM_DISPLAY).
_CANONICAL_MECHANISMS = {
    "subvencao", "credito", "bolsa", "matching", "equity", "premio", "outro",
}
_MECHANISM_SYNONYMS = {
    "investimento": "equity",
    "financiamento": "credito",
    "reembolsavel": "credito",
    "naoreembolsavel": "subvencao",
    "subvencaoeconomica": "subvencao",
    "pitch": "equity",
    "captacao": "equity",
    "embrapii": "matching",
}

# Rótulo de display por slug canônico (fonte única — usada pelo card de catálogo e
# pelo normalizador do extractor). `equity` = "Equity/Investimento" (desambigua a
# colisão histórica com crédito).
MECHANISM_DISPLAY = {
    "subvencao": "Subvenção",
    "credito": "Crédito",
    "bolsa": "Bolsa",
    "matching": "Matching",
    "equity": "Equity/Investimento",
    "premio": "Prêmio",
    "outro": "Outro",
}


def mechanism_display(slug: str) -> str:
    """Rótulo de display de um slug canônico de mecanismo (fallback = o próprio slug)."""
    return MECHANISM_DISPLAY.get(slug, slug)


def _deburr(s: str) -> str:
    """lowercase + remove acentos + colapsa não-alfanuméricos."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _normalize_source(source: str | None) -> str:
    if not source:
        return ""
    return source.lower().strip().replace(" ", "_")


def _normalize_mechanism(mechanism: str | None) -> str | None:
    """Resolve o mecanismo para um slug canônico, ou None se desconhecido/ausente.

    Se a string inteira não casar, tenta token por vírgula — cobre o caso de
    edital_card() concatenar múltiplos nós Mecanismo num único campo.
    """
    if not mechanism:
        return None
    key = _deburr(mechanism)
    if key in _CANONICAL_MECHANISMS:
        return key
    if key in _MECHANISM_SYNONYMS:
        return _MECHANISM_SYNONYMS[key]
    for part in mechanism.split(","):
        part_key = _deburr(part)
        if part_key in _CANONICAL_MECHANISMS:
            return part_key
        if part_key in _MECHANISM_SYNONYMS:
            return _MECHANISM_SYNONYMS[part_key]
    return None


# ── Parsing ─────────────────────────────────────────────────────────────────

def _parse_playbook_file(path: Path) -> tuple[str, dict[str, str]] | None:
    """Lê um arquivo de playbook → (lente, {seção: corpo}).

    Retorna None se ausente ou PLACEHOLDER (tratado como ausente).
    """
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("falha ao ler playbook %s: %s", path, e)
        return None
    if "PLACEHOLDER" in raw:
        logger.debug("playbook %s é placeholder — tratado como ausente", path.name)
        return None

    # Remove comentários HTML (cabeçalho SEED + anotações `<!-- → consumidor -->`).
    text = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)

    lente_buf: list[str] = []
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        nonlocal lente_buf
        if current is None:
            lente_buf = buf[:]
        else:
            sections[current] = "\n".join(buf).strip()

    for line in text.splitlines():
        s = line.strip()
        if s == "---":
            break  # rodapé fato↔craft e meta de autoria — fora do conteúdo roteável
        if s.startswith("## "):
            _flush()
            current = s[3:].strip()
            buf = []
            continue
        if s.startswith("# "):
            continue  # H1 (título) ignorado
        buf.append(line)
    _flush()

    lente = "\n".join(lente_buf).strip()
    return lente, sections


# ── Playbook composto ───────────────────────────────────────────────────────

@dataclass
class Playbook:
    """Playbook resolvido e composto, endereçável por seção."""
    mechanism: str | None
    source: str | None
    lente: str = ""
    sections: dict[str, str] = field(default_factory=dict)
    confidence: str = "ok"  # "ok" | "low" (mecanismo não identificado)

    def _render(self, order: tuple[str, ...], *, include_lente: bool) -> str:
        parts: list[str] = []
        if include_lente and self.lente:
            parts.append(f"## {SECTION_LENTE}\n{self.lente}")
        for name in order:
            body = self.sections.get(name)
            if body:
                parts.append(f"## {name}\n{body}")
        if not parts:
            return ""
        out = "\n\n".join(parts)
        if self.confidence == "low":
            out = f"{_LOW_CONF_MARKER}\n\n{out}"
        return out

    def for_writer(self) -> str:
        """Payload para o Redator: Lente + Padrões de escrita + Praxe da agência."""
        return self._render(_WRITER_ORDER, include_lente=True)

    def for_monitor(self) -> str:
        """Payload para o ComplianceMonitor: Heurísticas + Anti-padrões + Praxe."""
        return self._render(_MONITOR_ORDER, include_lente=False)

    def is_empty(self) -> bool:
        return not self.lente and not any(self.sections.values())


@dataclass
class PlaybookLayer:
    """Uma camada resolvida do playbook (para auditoria visual camada-por-camada).

    `layer` ∈ {"git_base", "git_source", "learned_overlay"}.
    """
    layer: str
    lente: str = ""
    sections: dict[str, str] = field(default_factory=dict)


# ── 4ª camada: learned overlays do banco (Item 3) ────────────────────────────
# Lidos via service-role (tabela GLOBAL/cross-workspace, sem RLS — ver
# migration 024). TODO acesso ao banco é GUARDADO: qualquer falha (sem DB
# configurado, query quebra, sem linhas) → retorna [] e o loader cai no caminho
# git-only de sempre, sem regressão.

def _load_overlays(mechanism: str, source: str) -> list[tuple[str, str]]:
    """Busca learned overlays para (mechanism, source) → [(section, body), ...].

    Casa overlays do mecanismo cujo `source` é exatamente `source` OU NULL
    (overlay vale para qualquer fonte). Retorna [] em QUALQUER falha — o acesso
    ao DB é totalmente opcional e nunca pode quebrar o caminho git-only.
    """
    if not mechanism:
        return []
    try:
        from core.db import get_supabase_service

        db = get_supabase_service()
        res = (
            db.table("playbook_overlays")
            .select("source, section, body, created_at")
            .eq("mechanism", mechanism)
            .order("created_at")
            .execute()
        )
        rows = res.data or []
    except Exception as e:  # sem DB / query falha / env ausente → git-only
        logger.debug("learned overlays indisponíveis para %s/%s: %s", mechanism, source, e)
        return []

    out: list[tuple[str, str]] = []
    for row in rows:
        row_src = _normalize_source(row.get("source"))
        # source NULL/"" no banco = vale para qualquer fonte; senão tem que casar.
        if row_src and row_src != source:
            continue
        section = (row.get("section") or "").strip()
        body = (row.get("body") or "").strip()
        if section and body:
            out.append((section, body))
    return out


def _load_git_layers(
    mechanism: str | None, source: str | None
) -> tuple[str | None, str, list[PlaybookLayer]]:
    """Resolve as camadas GIT (base + source) separadas, para merge ou auditoria.

    Retorna (mech_canônico, confidence, [camadas git na ordem de aplicação]).
    Réplica exata da resolução git-only de antes — não toca no banco.
    """
    mech = _normalize_mechanism(mechanism)
    src = _normalize_source(source)
    confidence = "ok"

    base = _parse_playbook_file(_SKILLS_DIR / "mechanism" / f"{mech}.md") if mech else None
    if base is None:
        confidence = "low"
        base = _parse_playbook_file(_SKILLS_DIR / "mechanism" / "_generic.md")

    layers: list[PlaybookLayer] = []
    if base:
        bl, bsecs = base
        layers.append(PlaybookLayer("git_base", lente=bl, sections={k: v for k, v in bsecs.items() if v}))

    if src and mech:
        src_lente = ""
        src_secs: dict[str, str] = {}
        for fname in ("global.md", f"{mech}.md"):
            parsed = _parse_playbook_file(_SKILLS_DIR / "source" / src / fname)
            if not parsed:
                continue
            lente, secs = parsed
            if lente and not src_lente:
                src_lente = lente
            for name, body in secs.items():
                if body:
                    # dentro da camada git_source, global.md + <mech>.md acumulam por seção
                    src_secs[name] = (src_secs[name] + "\n\n" + body) if name in src_secs else body
        if src_lente or src_secs:
            layers.append(PlaybookLayer("git_source", lente=src_lente, sections=src_secs))

    return mech, confidence, layers


def load_playbook(
    mechanism: str | None,
    source: str | None = None,
    *,
    include_overlays: bool = True,
) -> Playbook:
    """Compõe o playbook efetivo para (mechanism, source), merge por seção.

    Camadas, na ordem de aplicação (merge POR SEÇÃO; cada camada adiciona/acumula):
        git base (mechanism/<mech>.md)
          + git source (source/<src>/global.md + source/<src>/<mech>.md)
          + learned overlays do banco (playbook_overlays)  ← 4ª camada (Item 3)

    Backward-compat: `include_overlays` é keyword-only e default True, mas o acesso
    ao banco é totalmente GUARDADO (`_load_overlays` devolve [] em qualquer falha).
    Sem DB configurado → resultado idêntico ao caminho git-only de antes. Passe
    `include_overlays=False` para forçar git-only sem nem tocar no banco.

    Fallback gracioso: mechanism None/desconhecido → tenta `mechanism/_generic.md`,
    marca `confidence="low"` e NÃO bloqueia (D3). Camada/seção ausente é ignorada.
    """
    mech, confidence, git_layers = _load_git_layers(mechanism, source)
    src = _normalize_source(source)

    lente = ""
    merged: dict[str, list[str]] = {}

    for layer in git_layers:
        if layer.lente and not lente:
            lente = layer.lente
        for name, body in layer.sections.items():
            if body:
                merged.setdefault(name, []).append(body)

    # 4ª camada: learned overlays do banco, mergeados DEPOIS das camadas git.
    if include_overlays and mech:
        for name, body in _load_overlays(mech, src):
            merged.setdefault(name, []).append(body)

    sections = {name: "\n\n".join(parts) for name, parts in merged.items()}
    if sections:
        logger.info("tripwire: playbook_loaded mechanism=%s source=%s n_sections=%d confidence=%s",
                     mech, src or "(none)", len(sections), confidence)
    else:
        logger.warning("tripwire: playbook_missing mechanism=%s source=%s confidence=%s",
                       mech, src or "(none)", confidence)
    return Playbook(
        mechanism=mech,
        source=src or None,
        lente=lente,
        sections=sections,
        confidence=confidence,
    )


def resolve_playbook_layers(
    mechanism: str | None, source: str | None = None
) -> tuple[str | None, str | None, list[PlaybookLayer]]:
    """Resolve o playbook CAMADA POR CAMADA (git base, git source, learned overlay).

    Para a auditoria visual "o que veio de onde" (endpoint /playbooks/.../layers).
    NÃO mergeia — devolve cada camada separada na ordem de aplicação. O acesso ao
    banco é guardado (sem overlays / sem DB → só as camadas git).

    Retorna (mech_canônico, source_normalizado_ou_None, [PlaybookLayer, ...]).
    """
    mech, _confidence, layers = _load_git_layers(mechanism, source)
    src = _normalize_source(source)

    if mech:
        overlay_secs: dict[str, str] = {}
        for name, body in _load_overlays(mech, src):
            overlay_secs[name] = (overlay_secs[name] + "\n\n" + body) if name in overlay_secs else body
        if overlay_secs:
            layers.append(PlaybookLayer("learned_overlay", sections=overlay_secs))

    return mech, (src or None), layers


def available_skills() -> list[dict]:
    """Lista playbooks disponíveis (mecanismos + overlays de fonte). Debug/UI."""
    out: list[dict] = []
    mech_dir = _SKILLS_DIR / "mechanism"
    if mech_dir.exists():
        for f in sorted(mech_dir.glob("*.md")):
            out.append({
                "kind": "mechanism",
                "mechanism": f.stem,
                "file": str(f.relative_to(_SKILLS_DIR)),
                "size_bytes": f.stat().st_size,
            })
    src_dir = _SKILLS_DIR / "source"
    if src_dir.exists():
        for f in sorted(src_dir.glob("*/*.md")):
            out.append({
                "kind": "source_overlay",
                "source": f.parent.name,
                "mechanism": f.stem,
                "file": str(f.relative_to(_SKILLS_DIR)),
                "size_bytes": f.stat().st_size,
            })
    return out
