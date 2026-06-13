"""Scratchpad mínimo (write_note / read_note) — memória de trabalho por execução.

Padrão roubado do deepagents (ver docs/spec_agent_patterns.md): em loops que
leem muitas páginas com texto truncado (ex.: ProfileExtractor crawlando até 10
páginas), o agente perde fatos das páginas anteriores quando o contexto enche.
O scratchpad dá um lugar para ele anotar o que importa e reler antes de concluir.

Em memória, por execução, não persistido. Consumidor real = ProfileExtractor.

Princípios (vide core/llm/agent_runtime.py):
  • As tools NUNCA lançam exceção pro loop — limites estourados e nota
    inexistente viram string de erro.
  • Limites defensivos (max_notes, max_chars) impedem o agente de transformar
    o scratchpad num dump ilimitado de contexto.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.llm.agent_runtime import Tool, tool


@dataclass
class Scratchpad:
    """Bloco de notas em memória de uma execução.

    notes: nome → conteúdo. Mutável por closure: build_scratchpad_tools fecha
    sobre uma instância e as tools a mutam in-place.
    """
    notes: dict[str, str] = field(default_factory=dict)


def build_scratchpad_tools(
    pad: Scratchpad, *, max_notes: int = 20, max_chars: int = 4000,
) -> list[Tool]:
    """Constrói write_note + read_note fechando sobre `pad`.

    Args:
        pad: Scratchpad da execução (isolamento por closure).
        max_notes: teto de notas distintas (sobrescrever uma existente não conta).
        max_chars: teto de caracteres por nota.
    """

    @tool
    def write_note(name: str, content: str) -> str:
        """Anota um fato sob um nome para reler depois. Sobrescreve se o nome
        já existir.

        Use para reter fatos relevantes que você não quer perder quando o
        contexto encher — ex.: ao ler várias páginas, anote os achados de cada
        uma antes de seguir para a próxima. Releia com read_note antes de
        concluir/submeter.

        Args:
            name: rótulo curto da nota (ex.: "uf", "cnpj", "produtos").
            content: o conteúdo a guardar (texto conciso, não cole páginas
                inteiras — o limite é por nota).
        """
        name = (name or "").strip()
        if not name:
            return "Erro: 'name' não pode ser vazio."
        if len(content) > max_chars:
            return (
                f"Erro: nota '{name}' tem {len(content)} caracteres, acima do "
                f"limite de {max_chars}. Resuma antes de gravar."
            )
        if name not in pad.notes and len(pad.notes) >= max_notes:
            return (
                f"Erro: limite de {max_notes} notas atingido. Reaproveite um "
                f"nome existente ({', '.join(sorted(pad.notes))}) ou seja mais "
                "seletivo."
            )
        pad.notes[name] = content
        existentes = ", ".join(sorted(pad.notes))
        return f"Nota '{name}' salva. Notas atuais: {existentes}."

    @tool
    def read_note(name: str) -> str:
        """Lê uma nota gravada antes com write_note.

        Use para recuperar fatos que você anotou em passos anteriores — ex.:
        antes de submeter o resultado final, releia as notas para consolidar.

        Args:
            name: rótulo da nota a ler.
        """
        name = (name or "").strip()
        if name not in pad.notes:
            if not pad.notes:
                return "Nenhuma nota gravada ainda."
            return (
                f"Erro: nota '{name}' não existe. Notas disponíveis: "
                f"{', '.join(sorted(pad.notes))}."
            )
        return pad.notes[name]

    return [write_note, read_note]
