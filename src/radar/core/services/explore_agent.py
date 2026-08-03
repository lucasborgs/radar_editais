"""ExploreAgent — chat exploratório sobre o catálogo SQL (entities) + match.

Rota ÚNICA: agente ReAct multi-step com tools que leem o catálogo gold direto
(radar.core.kg.entity_catalog / SQL) — sem hipergrado, sem index.json/wiki. As tools
cobrem leitura (list_editais, get_edital, explore_opportunity, list_icts/
investidores), mapeamento do ecossistema (search_entities, related_by_tags,
get_node_neighborhood — §8 da spec v3-unified), match com perfil
(find_matching_editais / find_matching_entities) e memória entre sessões
(exploration_log).
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)


def _history_without_current(history: list[dict] | None, message: str) -> list[dict]:
    """Normaliza a fronteira: ``history`` só contém mensagens anteriores."""
    items = list(history or [])
    if items and items[-1].get("role") == "user" and items[-1].get("content") == message:
        items.pop()
    return items


@dataclass
class ExploreStreamEvent:
    """Evento do canal streaming de `ExploreAgent.explore_stream` (item 1,
    TASK 3). Contrato público do serviço — o router SSE não sabe nada sobre
    `AgentResult`/`StreamDelta` internos de `radar.core.llm.agent_graph`, mesma
    fronteira de encapsulamento que `explore_with_meta` já mantém hoje.

    kind == "token":    delta de texto do assistente (`text`).
    kind == "tool_end": uma tool terminou (`name`) — sinal leve p/ "pensando".
    kind == "final":    fim do turno — `answer` e `meta` têm o MESMO shape
                        que `explore_with_meta` retorna (`stop_reason`,
                        `truncated`, `called_match`, `called_tools`).
    """
    kind: Literal["token", "tool_end", "final"]
    text: str = ""
    name: str = ""
    answer: str = ""
    meta: dict = field(default_factory=dict)


KG_PHASE1_EXPLORE_SYSTEM = """Você é o assistente do Radar de Editais. Converse em português, de forma
direta, e ajude o usuário a explorar o grafo e pensar sua estratégia.

O grafo é a única fonte de fatos atuais. Para perguntas factuais sobre
oportunidades, ICTs, investidores, programas, agências ou suas conexões, use
somente graph_strategy, graph_explore, graph_reason e graph_community. Saudações
e explicações conceituais podem ser respondidas sem ferramenta.

Use o resultado das ferramentas deste turno como autoridade: `supporting_facts`
são fatos catalogados; `derived_steps`, similaridade e ponte tecnológica são
relações derivadas, não fatos confirmados; recomendações e prioridades são sua
análise estratégica. Deixe essa distinção clara na resposta, sem transformar
uma derivação em afirmação factual. Se o grafo não trouxer uma informação, diga
que ela é desconhecida no recorte consultado, sem inferir ou inventar.

O perfil autenticado já está disponível às ferramentas: não o peça, altere ou
fabrique. O histórico ajuda a conversa, mas não prova fatos atuais. Ao citar
uma entidade do grafo, use seu ID canônico quando isso ajudar a identificá-la."""


EXPLORE_SAFE_SYSTEM = """Você é o assistente do Radar de Editais. Converse em
português, de forma direta e útil. O grafo não está disponível nesta conversa;
não invente fatos atuais sobre oportunidades ou o ecossistema. Para perguntas
factuais, informe essa indisponibilidade com clareza. Saudações e explicações
conceituais podem ser respondidas normalmente."""


ANTHROPIC_MODEL_AGENT_EXPLORE = os.getenv(
    "ANTHROPIC_MODEL_AGENT_EXPLORE",
    os.getenv("ANTHROPIC_MODEL_AGENT", "claude-sonnet-4-6"),
)
EXPLORE_AGENT_MAX_STEPS = int(os.getenv("EXPLORE_AGENT_MAX_STEPS", "15"))
# Item 3 (TASK 3): janela de paridade do trim da thread-por-sessão do explore —
# preserva ~8 turnos (o mesmo `[-8:]` que o path stateless re-seedava).
EXPLORE_THREAD_HISTORY_WINDOW = int(os.getenv("EXPLORE_THREAD_HISTORY_WINDOW", "8"))


class ExploreAgent:
    """Chat exploratório sobre o hipergrado. Rota única = agente ReAct com tools."""

    def explore(
        self,
        message: str,
        history: list[dict] | None = None,
        edital_ids: list[str] | None = None,
        node_id: str | None = None,
        node_type: str | None = None,
        has_profile: bool = False,
        profile_text: str | None = None,
        workspace_id: str | None = None,
        db=None,
        profile: dict | None = None,
    ) -> str:
        answer, _meta = self.explore_with_meta(
            message, history, edital_ids, node_id, node_type,
            has_profile=has_profile, profile_text=profile_text,
            workspace_id=workspace_id, db=db, profile=profile,
        )
        return answer

    def explore_with_meta(
        self,
        message: str,
        history: list[dict] | None = None,
        edital_ids: list[str] | None = None,
        node_id: str | None = None,
        node_type: str | None = None,
        has_profile: bool = False,
        profile_text: str | None = None,
        workspace_id: str | None = None,
        db=None,
        profile: dict | None = None,
    ) -> tuple[str, dict]:
        """Como `explore`, mas devolve também metadados do run: `stop_reason` e
        `truncated` (= cortado no teto de passos, PR6.2/F10)."""
        return self._explore_agent(
            message, history, edital_ids, node_id, node_type,
            profile_text=profile_text, workspace_id=workspace_id, db=db, profile=profile,
        )

    async def explore_stream(
        self,
        message: str,
        history: list[dict] | None = None,
        edital_ids: list[str] | None = None,
        node_id: str | None = None,
        node_type: str | None = None,
        has_profile: bool = False,
        profile_text: str | None = None,
        workspace_id: str | None = None,
        db=None,
        profile: dict | None = None,
        thread_id: str | None = None,
    ) -> AsyncIterator[ExploreStreamEvent]:
        """Variação streaming de `explore_with_meta` (item 1, TASK 3).

        Mesmo shape de `(answer, meta)` no final —
        só o transporte muda: tokens chegam ao vivo via `ExploreStreamEvent`
        em vez de um retorno único. Deliberadamente DUPLICA o corpo de
        `_explore_agent` (system/tools) em vez de extrair um helper
        compartilhado — menor toque da TASK 3: `_explore_agent`/`explore_with_meta`
        ficam byte-idênticos, zero risco de regressão no caminho síncrono de
        produção. Custo aceito: as duas cópias podem divergir com o tempo — se
        isso incomodar, é candidato a refatoração numa task futura, não aqui.

        ATENÇÃO — cópia espelhada de `_explore_agent` (linha ~428): qualquer
        mudança de system/tools lá (ou aqui) tem que ser replicada
        manualmente no outro lado, ou o streaming e o sync divergem em
        comportamento (não só em transporte). Unificação prevista pra TASK 6.
        """
        from radar.core.llm.agent_runtime import resolve_agent_provider, run_agent_streaming_async

        # Item 3 (TASK 3) — thread-por-sessão do explore. `thread_id` chega PRONTO do
        # router (`{ws}:{session}`, exige workspace autenticado + sessão; preserva o
        # prefixo `{ws}` do leak-test). Recebê-lo pronto — em vez de derivar de
        # `workspace_id` aqui — mantém a T3 ORTOGONAL ao wiring de tools: `workspace_id`
        # segue alimentando match/log exatamente como hoje (o streaming não muda de
        # ferramentas). Explore anônimo/sem sessão → `thread_id=None` → caminho
        # stateless de HOJE (re-seeda `[-8:]`), byte-idêntico.
        saver = None
        prior_n_msgs = 0
        system_msg_id = None
        hint = self._build_explore_hint(edital_ids, node_id, node_type)
        messages: list[dict] = []

        if thread_id is not None:
            from radar.core.llm.agent_graph import (
                EXPLORE_SYSTEM_MSG_ID,
                aget_thread_message_count,
                atrim_thread_history,
                get_explore_checkpointer,
            )
            saver = await get_explore_checkpointer()
            if saver is None:
                thread_id = None  # init degradou → stateless (sem quebrar o turno)

        if thread_id is not None:
            system_msg_id = EXPLORE_SYSTEM_MSG_ID
            # Poda na fronteira do turno (paridade com `[-8:]`), best-effort, ANTES
            # de ler o count (o delta fatia a partir do estado já podado).
            await atrim_thread_history(
                saver, thread_id,
                keep_human_turns=EXPLORE_THREAD_HISTORY_WINDOW,
                keep_ids=(EXPLORE_SYSTEM_MSG_ID,),
            )
            prior_n_msgs = await aget_thread_message_count(saver, thread_id)
            # Uma thread vazia pode surgir no segundo turno, depois que o
            # primeiro par só existiu no transcript do cliente. Semeie-o junto
            # com o turno atual; o delta começa depois desse prefixo.
            if prior_n_msgs == 0:
                for turn in _history_without_current(history, message)[-8:]:
                    role = turn.get("role")
                    content = turn.get("content")
                    if role in ("user", "assistant") and content:
                        messages.append({"role": role, "content": content})
                prior_n_msgs = 1 + len(messages)
            # O hint é contexto episódico do alvo e vai apenas na mensagem
            # atual, sem acumular na thread durável.
            content = f"{hint}\n\n{message}" if hint else message
            messages.append({"role": "user", "content": content, "cache_hint": True})
        else:
            # Stateless (anônimo/sem sessão): caminho de hoje, byte-idêntico.
            for turn in _history_without_current(history, message)[-8:]:
                role = turn.get("role")
                content = turn.get("content")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
            if hint:
                messages.append({"role": "user", "content": hint})
            messages.append({"role": "user", "content": message, "cache_hint": True})

        tools = self._explore_tools(profile=profile, db=db)
        system = self._explore_system()

        from radar.core.kg.phase1.tools import graph_tools_enabled
        if not graph_tools_enabled():
            system += "\n\nO grafo está desligado. Para perguntas factuais sobre o ecossistema, informe indisponibilidade segura; não fabrique fatos."

        provider, model = resolve_agent_provider(
            "anthropic", ANTHROPIC_MODEL_AGENT_EXPLORE,
        )

        result = None
        streamed_text = ""
        async for delta in run_agent_streaming_async(
            system=system,
            initial_messages=messages,
            tools=tools,
            model=model,
            provider=provider,
            max_steps=EXPLORE_AGENT_MAX_STEPS,
            mode="explore",
            thread_id=thread_id,
            checkpointer=saver,
            prior_n_msgs=prior_n_msgs,
            system_msg_id=system_msg_id,
        ):
            if delta.kind == "token":
                if delta.text:
                    streamed_text += delta.text
            elif delta.kind == "tool_end":
                yield ExploreStreamEvent(kind="tool_end", name=delta.name)
            elif delta.kind == "done":
                result = delta.result
            # kind == "error": o "done" seguinte já carrega o AgentResult
            # degradado (mesmo contrato de run_agent_graph_streaming) — nada
            # extra a fazer aqui.

        if result is None:
            # Não deveria disparar: run_agent_graph_streaming SEMPRE emite um
            # "done" antes de terminar (inclusive nos caminhos de erro) — mas
            # não propaga exceção crua nem confia em invariante silenciosa
            # (mesma lição do fix de `agent_graph.py`: nunca um `assert` nu
            # no meio do generator).
            logger.error("explore agent (stream): terminou sem evento 'done'")
            yield ExploreStreamEvent(
                kind="final",
                answer="Desculpe, não consegui processar agora. Tente novamente em instantes.",
                meta={"stop_reason": "error", "truncated": False, "called_match": False},
            )
            return

        if result is not None and not result.final_text:
            result.final_text = streamed_text
        answer = result.final_text or streamed_text
        repaired = False
        called_match = any(
            s.kind == "tool"
            and s.name in ("find_matching_editais", "find_matching_entities")
            for s in result.steps
        )
        called_tools = [s.name for s in result.steps if s.kind == "tool" and s.name]
        meta = {
            "stop_reason": result.stop_reason,
            "truncated": result.stop_reason == "max_steps",
            "called_match": called_match,
            "called_tools": called_tools,
            "repair_triggered": repaired,
            "fallback": answer.startswith("Não foi possível validar"),
        }

        if result.stop_reason == "error":
            logger.error("explore agent (stream): stop_reason=error após %d steps", len(result.steps))
            answer = "Desculpe, não consegui processar agora. Tente novamente em instantes."
        else:
            answer = answer or "Não consegui formular uma resposta agora."

        if answer:
            yield ExploreStreamEvent(kind="token", text=answer)
        yield ExploreStreamEvent(kind="final", answer=answer, meta=meta)

    def _explore_tools(self, profile: dict | None = None, db=None) -> list:
        """Tools do agente de explore: leitura do catálogo gold, opcionalmente
        deep_research (subagente web) e — gated por `KG_PHASE1_EXPLORE_ENABLED` —
        as tools read-only do grafo da Fase 1 (ADITIVAS; `profile` vai na
        closure de graph_reason). Flag off = ferramentas exatamente como antes."""
        from radar.core.kg.phase1.tools import build_graph_tools, graph_tools_enabled
        if graph_tools_enabled():
            from langchain_core.tools import StructuredTool

            from radar.core.services.grounded_strategy import temporalize_tool_payload

            wrapped = []
            for graph_tool in build_graph_tools(profile=profile):
                def invoke_graph(_tool=graph_tool, **kwargs):
                    return temporalize_tool_payload(_tool.invoke(kwargs), db=db)

                wrapped.append(StructuredTool.from_function(
                    invoke_graph,
                    name=graph_tool.name,
                    description=getattr(graph_tool, "description", graph_tool.name),
                    args_schema=getattr(graph_tool, "args_schema", None),
                ))
            # Modo exclusivo: nenhuma tool de catálogo, Match, web ou memória.
            return wrapped

        return []

    @staticmethod
    def _explore_system() -> str:
        """Seleciona um system seguro; o modo KG nunca recebe o prompt legado."""
        from radar.core.kg.phase1.tools import graph_tools_enabled
        if graph_tools_enabled():
            return KG_PHASE1_EXPLORE_SYSTEM
        return EXPLORE_SAFE_SYSTEM

    def _explore_agent(
        self,
        message: str,
        history: list[dict] | None,
        edital_ids: list[str] | None,
        node_id: str | None,
        node_type: str | None,
        profile_text: str | None = None,
        workspace_id: str | None = None,
        db=None,
        profile: dict | None = None,
    ) -> tuple[str, dict]:
        """Pipeline agente: run_agent + tools de leitura do catálogo gold,
        planejamento e (gated) deep_research — montadas em `_explore_tools`.
        Retorna `(answer, meta)`; meta carrega stop_reason/truncated.

        ATENÇÃO — `explore_stream` (linha ~218) duplica este setup (system/
        tools) pro caminho streaming. Mudança aqui tem que ser replicada
        lá também, ou os dois caminhos divergem em comportamento (não só
        transporte). Unificação prevista pra TASK 6."""
        from radar.core.llm.agent_runtime import resolve_agent_provider, run_agent

        # Item 3 (TASK 3): a promoção thread-por-sessão é do caminho VIVO (streaming,
        # `explore_stream`). Esta cópia sync (`/explore` não-streaming) fica
        # DELIBERADAMENTE stateless — re-seeda `[-8:]` como sempre. Migrá-la exigiria
        # cruzar o saver loop-local pro `run_agent` sync (outro loop) sem ganho: o
        # front usa o streaming. Unificação das duas cópias fica pra TASK 6.
        #
        # GAP ACEITO (governança, revisão T3): um turno atendido por ESTE espelho
        # sync NÃO escreve na thread `{ws}:{session}`. Se um turno cair aqui (ex.:
        # cliente não-streaming) e o SEGUINTE for pelo stream, o agente não verá o
        # turno-sync na thread → amnésia de 1 turno. Aceito porque: (a) é raro (o
        # front usa `/explore/stream`); (b) degradação graciosa (o pior é re-perguntar,
        # não corromper); (c) `session_turns` (persist_turn) preserva o registro de
        # produto — só o CONTEXTO do agente perde aquele turno, não o histórico do
        # usuário. Fechar o gap = unificar as cópias (TASK 6).
        messages: list[dict] = []
        for turn in _history_without_current(history, message)[-8:]:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        hint = self._build_explore_hint(edital_ids, node_id, node_type)
        if hint:
            messages.append({"role": "user", "content": hint})

        # Breakpoint de cache (PR2 §2.2): marca a mensagem do usuário atual — as
        # iterações 2..N do mesmo turno ReAct leem todo o prefixo (system+history+
        # hint) do cache. O system ganha o próprio breakpoint no consumidor
        # (`agent_graph._build_system_message`). Não há bloco de perfil como
        # mensagem aqui: o perfil vai na closure das match tools, não no prompt.
        # A flag é consumida em `_to_lc_messages` e só vira `cache_control` com
        # provider == "anthropic"; nos demais é ignorada (no-op).
        messages.append({"role": "user", "content": message, "cache_hint": True})

        tools = self._explore_tools(profile=profile, db=db)
        system = self._explore_system()

        # Match v3: só quando há PERFIL. COM workspace autenticado, o lado
        # empresa vem de company_chunks (perfil + library, refresh on-demand);
        # SEM workspace (explore público stateless), o perfil do request vira
        # chunks efêmeros (cache por hash). Em ambos os casos o funil rankeia
        # editais/entidades por afinidade de texto real.
        from radar.core.kg.phase1.tools import graph_tools_enabled
        if not graph_tools_enabled():
            system += "\n\nO grafo está desligado. Para perguntas factuais sobre o ecossistema, informe indisponibilidade segura; não fabrique fatos."

        provider, model = resolve_agent_provider(
            "anthropic", ANTHROPIC_MODEL_AGENT_EXPLORE,
        )
        result = run_agent(
            system=system,
            initial_messages=messages,
            tools=tools,
            model=model,
            provider=provider,
            max_steps=EXPLORE_AGENT_MAX_STEPS,
            mode="explore",
        )

        answer = result.final_text or ""
        repaired = False
        called_match = any(
            s.kind == "tool"
            and s.name in ("find_matching_editais", "find_matching_entities")
            for s in result.steps
        )
        called_tools = [s.name for s in result.steps if s.kind == "tool" and s.name]
        meta = {
            "stop_reason": result.stop_reason,
            "truncated": result.stop_reason == "max_steps",
            "called_match": called_match,
            "called_tools": called_tools,
            "repair_triggered": repaired,
            "fallback": answer.startswith("Não foi possível validar"),
        }
        if result.stop_reason == "error":
            logger.error("explore agent: stop_reason=error após %d steps", len(result.steps))
            return (
                "Desculpe, não consegui processar agora. Tente novamente em instantes.",
                meta,
            )

        return answer or "Não consegui formular uma resposta agora.", meta

    @staticmethod
    def _build_explore_hint(
        edital_ids: list[str] | None,
        node_id: str | None,
        node_type: str | None,
    ) -> str:
        """Constrói hint textual do clique no grafo para o agente."""
        parts: list[str] = []
        if node_id and node_type:
            parts.append(
                f"[Contexto: o visitante está focado no nó '{node_id}' "
                f"(tipo={node_type}). Considere usar get_node_neighborhood com esse "
                f"nome de nó, conforme a pergunta.]"
            )
        if edital_ids:
            ids_str = ", ".join(str(i) for i in edital_ids[:3])
            parts.append(
                f"[Contexto: visitante mencionou ou clicou nos editais: {ids_str}. "
                f"Considere usar get_edital ou get_node_neighborhood nesses IDs.]"
            )
        return "\n".join(parts)
