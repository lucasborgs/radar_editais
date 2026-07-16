"""Rate limiting (slowapi) — proteção de custo LLM.

Módulo próprio para que os routers importem `limiter` sem importar `backend.api`
(evitando import circular: api importa os routers). O handler de
RateLimitExceeded e o `app.state.limiter` são registrados em backend/api.py.
"""

from __future__ import annotations

import jwt
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def get_client_ip(request: Request) -> str:
    """IP real do visitante, para particionar rate limit anônimo por pessoa.

    Em produção o backend só é alcançado via Cloudflare Tunnel — sem isso,
    `get_remote_address` enxerga o peer TCP direto (o container do túnel),
    então todo tráfego anônimo cai no mesmo bucket. `Cf-Connecting-Ip` é
    setado pelo edge da Cloudflare com o IP real do visitante e não pode ser
    forjado por quem entra pelo túnel.
    """
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip
    return get_remote_address(request)


def _rate_limit_key(request: Request) -> str:
    """Particiona o rate limit por usuário autenticado; cai para IP se anônimo.

    A assinatura do JWT NÃO é verificada aqui — isso serve apenas para escolher
    a chave do bucket. A validação real do token acontece em cada endpoint via
    CurrentUserId.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            payload = jwt.decode(auth[7:], options={"verify_signature": False})
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except Exception:
            pass
    return get_client_ip(request)


limiter = Limiter(key_func=_rate_limit_key)
