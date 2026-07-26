"""Read model do funil editorial, lacunas e domínios emergentes (RT03-T05).

Deriva métricas de ``source_runs`` e ``discovered_opportunities`` sem
escrever no staging, sem criar fonte/scraper automático e sem inferir
atribuição para registros legados.

Todas as funções são puras: recebem listas de dicionários e uma data de
referência. Não acessam rede, DB, LLM ou filesystem.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# ──────────────────────────────────────────────────────────────────────────
# DTOs
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class ChannelRunMetrics:
    last_attempt: datetime | None
    last_success: datetime | None
    total_records_observed: int | None
    total_records_emitted: int | None
    total_records_staged: int | None
    yield_rate: float | None


@dataclass
class ChannelEditorialFunnel:
    source_key: str
    approved: int = 0
    rejected: int = 0
    pending: int = 0
    approval_rate: float | None = None
    avg_review_hours: float | None = None


@dataclass
class FamilyEditorialFunnel:
    family_key: str
    approved: int = 0
    rejected: int = 0
    pending: int = 0
    approval_rate: float | None = None
    avg_review_hours: float | None = None


@dataclass
class CoverageGap:
    source_key: str | None
    signal: str


@dataclass
class EmergingDomain:
    domain: str
    approval_count: int
    first_approved_at: datetime
    last_approved_at: datetime
    candidate_for_dedicated_monitoring: bool


@dataclass
class SourceCoverageReport:
    reference_date: datetime
    channel_runs: dict[str, ChannelRunMetrics] = field(default_factory=dict)
    channel_funnel: dict[str, ChannelEditorialFunnel] = field(default_factory=dict)
    family_funnel: dict[str, FamilyEditorialFunnel] = field(default_factory=dict)
    channel_health: dict[str, str] = field(default_factory=dict)
    gaps: list[CoverageGap] = field(default_factory=list)
    emerging_domains: list[EmergingDomain] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _parse_timestamp(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        timestamp = val
    elif isinstance(val, str):
        try:
            timestamp = datetime.fromisoformat(val)
        except (ValueError, TypeError):
            return None
    else:
        return None
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _safe_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        v = int(val)
        return v if v >= 0 else None
    except (ValueError, TypeError):
        return None


# ──────────────────────────────────────────────────────────────────────────
# 1. Runs e rendimento
# ──────────────────────────────────────────────────────────────────────────


def compute_channel_run_metrics(
    runs: list[dict[str, Any]],
) -> ChannelRunMetrics:
    """Agrega runs de um canal: última tentativa/sucesso e totais.

    As datas retornadas são normalizadas para UTC.
    """
    last_attempt: datetime | None = None
    last_success: datetime | None = None
    total_observed = 0
    total_emitted = 0
    total_staged = 0
    has_observed = False
    has_emitted = False
    has_staged = False

    for r in runs:
        started = _parse_timestamp(r.get("started_at"))
        if started is not None and (last_attempt is None or started > last_attempt):
            last_attempt = started

        completed = _parse_timestamp(r.get("completed_at"))
        status = r.get("status", "")
        if completed is not None and status == "succeeded":
            if last_success is None or completed > last_success:
                last_success = completed

        obs = _safe_int(r.get("records_observed"))
        if obs is not None:
            total_observed += obs
            has_observed = True

        emit = _safe_int(r.get("records_emitted"))
        if emit is not None:
            total_emitted += emit
            has_emitted = True

        staged = _safe_int(r.get("records_staged"))
        if staged is not None:
            total_staged += staged
            has_staged = True

    yield_rate: float | None = None
    if has_emitted and total_emitted > 0:
        yield_rate = total_staged / total_emitted

    return ChannelRunMetrics(
        last_attempt=last_attempt,
        last_success=last_success,
        total_records_observed=total_observed if has_observed else None,
        total_records_emitted=total_emitted if has_emitted else None,
        total_records_staged=total_staged if has_staged else None,
        yield_rate=yield_rate,
    )


# ──────────────────────────────────────────────────────────────────────────
# 2. Funil editorial
# ──────────────────────────────────────────────────────────────────────────


def compute_channel_editorial_funnel(
    channel_key: str,
    discovered: list[dict[str, Any]],
) -> ChannelEditorialFunnel:
    """Agrega decisões editoriais de ``discovered_opportunities`` por canal."""
    funnel = ChannelEditorialFunnel(source_key=channel_key)
    total_review_hours: float = 0.0
    review_count = 0

    for row in discovered:
        ch = row.get("discovery_channel")
        if ch == channel_key:
            status = row.get("status", "")
            if status == "promoted":
                funnel.approved += 1
            elif status == "rejected":
                funnel.rejected += 1
            else:
                funnel.pending += 1

            reviewed_at = _parse_timestamp(row.get("reviewed_at"))
            created_at = _parse_timestamp(row.get("created_at"))
            if reviewed_at is not None and created_at is not None:
                delta = (reviewed_at - created_at).total_seconds() / 3600
                if delta >= 0:
                    total_review_hours += delta
                    review_count += 1
    funnel.approval_rate = _compute_approval_rate(funnel.approved, funnel.rejected)
    funnel.avg_review_hours = (total_review_hours / review_count) if review_count > 0 else None
    return funnel


def compute_channel_editorial_funnels(
    discovered: list[dict[str, Any]],
    channel_keys: list[str],
) -> dict[str, ChannelEditorialFunnel]:
    """Agrega decisões editoriais por canal, incluindo bucket não atribuído."""
    unassigned: list[dict[str, Any]] = []
    channel_rows: dict[str, list[dict[str, Any]]] = {k: [] for k in channel_keys}

    for row in discovered:
        ch = row.get("discovery_channel")
        if ch is not None and ch in channel_rows:
            channel_rows[ch].append(row)
        elif ch is None:
            unassigned.append(row)

    result: dict[str, ChannelEditorialFunnel] = {}
    for k in channel_keys:
        result[k] = compute_channel_editorial_funnel(k, channel_rows[k])

    unassigned_funnel = ChannelEditorialFunnel(source_key="__unassigned__")
    for row in unassigned:
        status = row.get("status", "")
        if status == "promoted":
            unassigned_funnel.approved += 1
        elif status == "rejected":
            unassigned_funnel.rejected += 1
        else:
            unassigned_funnel.pending += 1
    unassigned_funnel.approval_rate = _compute_approval_rate(
        unassigned_funnel.approved, unassigned_funnel.rejected
    )
    unassigned_funnel.avg_review_hours = _compute_avg_review_hours(unassigned)
    result["__unassigned__"] = unassigned_funnel

    return result


def compute_family_editorial_funnels(
    discovered: list[dict[str, Any]],
    family_keys: list[str],
) -> dict[str, FamilyEditorialFunnel]:
    """Agrega decisões editoriais por família de query."""
    family_rows: dict[str, list[dict[str, Any]]] = {k: [] for k in family_keys}
    unassigned: list[dict[str, Any]] = []

    for row in discovered:
        fam = row.get("query_family")
        if fam is not None and fam in family_rows:
            family_rows[fam].append(row)
        elif fam is None:
            unassigned.append(row)

    result: dict[str, FamilyEditorialFunnel] = {}
    for k in family_keys:
        rows = family_rows[k]
        funnel = FamilyEditorialFunnel(family_key=k)
        total_review_hours = 0.0
        review_count = 0
        for row in rows:
            status = row.get("status", "")
            if status == "promoted":
                funnel.approved += 1
            elif status == "rejected":
                funnel.rejected += 1
            else:
                funnel.pending += 1
            reviewed_at = _parse_timestamp(row.get("reviewed_at"))
            created_at = _parse_timestamp(row.get("created_at"))
            if reviewed_at is not None and created_at is not None:
                delta = (reviewed_at - created_at).total_seconds() / 3600
                if delta >= 0:
                    total_review_hours += delta
                    review_count += 1
        funnel.approval_rate = _compute_approval_rate(funnel.approved, funnel.rejected)
        funnel.avg_review_hours = (total_review_hours / review_count) if review_count > 0 else None
        result[k] = funnel
    unassigned_funnel = FamilyEditorialFunnel(family_key="__unassigned__")
    for row in unassigned:
        status = row.get("status", "")
        if status == "promoted":
            unassigned_funnel.approved += 1
        elif status == "rejected":
            unassigned_funnel.rejected += 1
        else:
            unassigned_funnel.pending += 1
    unassigned_funnel.approval_rate = _compute_approval_rate(
        unassigned_funnel.approved, unassigned_funnel.rejected
    )
    unassigned_funnel.avg_review_hours = _compute_avg_review_hours(unassigned)
    result["__unassigned__"] = unassigned_funnel
    return result


def _compute_approval_rate(approved: int, rejected: int) -> float | None:
    denominator = approved + rejected
    if denominator <= 0:
        return None
    return approved / denominator


def _compute_avg_review_hours(rows: list[dict[str, Any]]) -> float | None:
    total = 0.0
    count = 0
    for row in rows:
        reviewed_at = _parse_timestamp(row.get("reviewed_at"))
        created_at = _parse_timestamp(row.get("created_at"))
        if reviewed_at is not None and created_at is not None:
            delta = (reviewed_at - created_at).total_seconds() / 3600
            if delta >= 0:
                total += delta
                count += 1
    return (total / count) if count > 0 else None


# ──────────────────────────────────────────────────────────────────────────
# 3. Saúde
# ──────────────────────────────────────────────────────────────────────────


def _is_channel_enabled(
    channel: dict[str, Any], env: dict[str, str] | None = None
) -> bool:
    """Verifica se o canal está habilitado considerando flag e default."""
    flag_name = channel.get("flag_name")
    if flag_name:
        actual_env = env if env is not None else os.environ
        return actual_env.get(flag_name, "0") == "1"
    return channel.get("enabled_by_default", True)


def _has_observable_result(run: dict[str, Any]) -> bool:
    obs = _safe_int(run.get("records_observed"))
    if obs is not None and obs > 0:
        return True
    staged = _safe_int(run.get("records_staged"))
    if staged is not None and staged > 0:
        return True
    return False


def derive_channel_health(
    channel: dict[str, Any],
    runs: list[dict[str, Any]],
    ref_dt: datetime,
    env: dict[str, str] | None = None,
) -> str:
    """Deriva estado de saúde de um canal (precedência da spec)."""
    if not _is_channel_enabled(channel, env):
        return "disabled"

    reference = _parse_timestamp(ref_dt)
    if reference is None:
        return "unknown"
    interval_h = channel.get("expected_interval_hours", 24)

    if not runs:
        return "unknown"

    last_run = max(
        runs,
        key=lambda run: _parse_timestamp(run.get("started_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    status = last_run.get("status", "")

    if status == "failed":
        return "failing"

    if status == "partial":
        return "degraded"

    latest_healthy: datetime | None = None
    for r in runs:
        if r.get("status", "") == "succeeded" and _has_observable_result(r):
            completed = _parse_timestamp(r.get("completed_at"))
            if completed is not None and (
                latest_healthy is None or completed > latest_healthy
            ):
                latest_healthy = completed

    if latest_healthy is not None:
        elapsed = (reference - latest_healthy).total_seconds() / 3600
        if elapsed >= 2 * interval_h:
            return "stale"

    if status == "succeeded" and _has_observable_result(last_run):
        completed = _parse_timestamp(last_run.get("completed_at"))
        if completed is not None:
            elapsed = (reference - completed).total_seconds() / 3600
            if 0 <= elapsed <= interval_h:
                return "healthy"

    return "unknown"


def derive_channel_healths(
    channels: list[dict[str, Any]],
    all_runs: dict[str, list[dict[str, Any]]],
    ref_dt: datetime,
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Deriva saúde de todos os canais."""
    result: dict[str, str] = {}
    for ch in channels:
        key = ch["source_key"]
        result[key] = derive_channel_health(ch, all_runs.get(key, []), ref_dt, env)
    return result


# ──────────────────────────────────────────────────────────────────────────
# 4. Lacunas
# ──────────────────────────────────────────────────────────────────────────


def detect_gaps(
    channels: list[dict[str, Any]],
    all_runs: dict[str, list[dict[str, Any]]],
    discovered: list[dict[str, Any]],
    family_keys: list[str],
    ref_dt: datetime,
    env: dict[str, str] | None = None,
) -> list[CoverageGap]:
    """Detecta sinais explícitos de lacuna."""
    reference = _parse_timestamp(ref_dt)
    if reference is None:
        return []
    gaps: list[CoverageGap] = []

    for ch in channels:
        key = ch["source_key"]
        enabled = _is_channel_enabled(ch, env)
        runs = all_runs.get(key, [])

        if enabled and not runs:
            gaps.append(CoverageGap(source_key=key, signal="enabled_no_run"))
            continue

        if runs:
            last_run = max(
                runs,
                key=lambda run: _parse_timestamp(run.get("started_at"))
                or datetime.min.replace(tzinfo=timezone.utc),
            )
            status = last_run.get("status", "")
            if status in ("succeeded", "partial") and not _has_observable_result(last_run):
                gaps.append(CoverageGap(source_key=key, signal="ambiguous_run"))

            interval_h = ch.get("expected_interval_hours", 24)
            completed = _parse_timestamp(last_run.get("completed_at"))
            if completed is not None and enabled:
                elapsed = (reference - completed).total_seconds() / 3600
                if elapsed > interval_h:
                    gaps.append(CoverageGap(source_key=key, signal="delayed"))

    for fam_key in family_keys:
        reviewed = 0
        for row in discovered:
            if row.get("query_family") == fam_key:
                status = row.get("status", "")
                if status in ("promoted", "rejected"):
                    reviewed += 1
        if reviewed == 0:
            gaps.append(CoverageGap(source_key=fam_key, signal="family_no_denominator"))

    pending_count = sum(
        1 for row in discovered if row.get("status") == "pending"
    )
    if pending_count > 0:
        gaps.append(CoverageGap(source_key=None, signal="pending_queue"))

    return gaps


# ──────────────────────────────────────────────────────────────────────────
# 5. Domínios emergentes
# ──────────────────────────────────────────────────────────────────────────


EMERGING_DOMAIN_DAYS = 90
EMERGING_DOMAIN_THRESHOLD = 2


def compute_emerging_domains(
    discovered: list[dict[str, Any]],
    ref_dt: datetime,
    *,
    window_days: int = EMERGING_DOMAIN_DAYS,
    threshold: int = EMERGING_DOMAIN_THRESHOLD,
) -> list[EmergingDomain]:
    """Identifica domínios com oportunidades aprovadas recorrentes.

    Considera exclusivamente:
    - ``origin_domain`` válido
    - status ``promoted``
    - ``reviewed_at`` nos últimos ``window_days`` dias
    """
    reference = _parse_timestamp(ref_dt)
    if reference is None:
        return []
    cutoff = reference - timedelta(days=window_days)
    domain_approvals: dict[str, list[datetime]] = {}

    for row in discovered:
        domain = _normalize_hostname(row.get("origin_domain"))
        if domain is None:
            continue
        if row.get("status") != "promoted":
            continue
        reviewed_at = _parse_timestamp(row.get("reviewed_at"))
        if reviewed_at is None:
            continue
        if reviewed_at < cutoff or reviewed_at > reference:
            continue

        if domain not in domain_approvals:
            domain_approvals[domain] = []
        domain_approvals[domain].append(reviewed_at)

    result: list[EmergingDomain] = []
    for domain, datetimes in domain_approvals.items():
        sorted_dts = sorted(datetimes)
        count = len(sorted_dts)
        result.append(
            EmergingDomain(
                domain=domain,
                approval_count=count,
                first_approved_at=sorted_dts[0],
                last_approved_at=sorted_dts[-1],
                candidate_for_dedicated_monitoring=count >= threshold,
            )
        )

    result.sort(key=lambda d: d.approval_count, reverse=True)
    return result


def _normalize_hostname(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    hostname = value.lower().rstrip(".")
    if not hostname or len(hostname) > 253:
        return None
    labels = hostname.split(".")
    for label in labels:
        if not label or len(label) > 63:
            return None
        if not label[0].isalnum() or not label[-1].isalnum():
            return None
        if any(not (char.isascii() and (char.isalnum() or char == "-")) for char in label):
            return None
    return hostname


# ──────────────────────────────────────────────────────────────────────────
# 6. Agregador principal
# ──────────────────────────────────────────────────────────────────────────


def compute_source_coverage(
    runs: list[dict[str, Any]],
    discovered: list[dict[str, Any]],
    channels: list[dict[str, Any]],
    family_keys: list[str],
    ref_dt: datetime | None = None,
    env: dict[str, str] | None = None,
) -> SourceCoverageReport:
    """Computa o relatório completo de cobertura da Descoberta.

    Parâmetros:
        runs: linhas de ``source_runs``
        discovered: linhas de ``discovered_opportunities``
        channels: lista de canais (``coverage_channels()``)
        family_keys: lista de chaves de família (``query_families()``)
        ref_dt: data de referência (default: now UTC)
        env: dict de env vars para verificação de flags (default: os.environ)
    """
    if ref_dt is None:
        ref_dt = datetime.now(timezone.utc)
    else:
        ref_dt = _parse_timestamp(ref_dt)
        if ref_dt is None:
            raise ValueError("ref_dt must be a valid datetime")

    # Agrupa runs por source_key
    runs_by_channel: dict[str, list[dict[str, Any]]] = {}
    for r in runs:
        sk = r.get("source_key", "")
        if sk not in runs_by_channel:
            runs_by_channel[sk] = []
        runs_by_channel[sk].append(r)

    # Ordena runs por started_at desc
    for sk in runs_by_channel:
        runs_by_channel[sk].sort(
            key=lambda x: _parse_timestamp(x.get("started_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

    report = SourceCoverageReport(reference_date=ref_dt)

    # 1. Run metrics
    for ch in channels:
        sk = ch["source_key"]
        report.channel_runs[sk] = compute_channel_run_metrics(
            runs_by_channel.get(sk, [])
        )

    # 2. Editorial funnel
    all_channel_keys = [ch["source_key"] for ch in channels]
    report.channel_funnel = compute_channel_editorial_funnels(
        discovered, all_channel_keys
    )
    report.family_funnel = compute_family_editorial_funnels(
        discovered, family_keys
    )

    # 3. Health
    report.channel_health = derive_channel_healths(
        channels, runs_by_channel, ref_dt, env
    )

    # 4. Gaps
    report.gaps = detect_gaps(
        channels, runs_by_channel, discovered, family_keys, ref_dt, env
    )

    # 5. Emerging domains
    report.emerging_domains = compute_emerging_domains(discovered, ref_dt)

    return report
