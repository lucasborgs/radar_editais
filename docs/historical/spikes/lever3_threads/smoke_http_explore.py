"""Smoke HTTP T3 (throwaway, gate de merge): /explore/stream multi-turno com app
de pé. Cunha um JWT do usuário seed (HS256/SUPABASE_JWT_SECRET), POSTa 2 turnos
na MESMA session_id autenticada e verifica memória via thread-por-sessão.

Requer uvicorn em 127.0.0.1:8001 (radar.api.app:app) + Postgres local seedado.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

import httpx
import jwt

BASE = "http://127.0.0.1:8001"
USER = "aee5a3b3-b9b4-44a2-b793-7f41721fbaca"  # supabase/seed.sql
SECRET = os.environ["SUPABASE_JWT_SECRET"]
FACT = "Meu projeto se chama Zephyr-9 e atua em energia eólica offshore."


def _token() -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": USER, "aud": "authenticated", "role": "authenticated",
         "iat": now, "exp": now + 3600},
        SECRET, algorithm="HS256",
    )


def _turn(client, token, session_id, message) -> str:
    """POSTa um turno SSE e devolve o `answer` do frame `done`."""
    answer = ""
    with client.stream(
        "POST", f"{BASE}/explore/stream",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": message, "session_id": session_id, "history": []},
    ) as r:
        r.raise_for_status()
        event = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event == "done":
                data = json.loads(line.split(":", 1)[1].strip())
                answer = data.get("answer", "")
    return answer


def main() -> int:
    token = _token()
    session_id = f"smoke-http-{uuid.uuid4().hex[:8]}"
    with httpx.Client(timeout=120) as client:
        a1 = _turn(client, token, session_id, FACT + " Guarde isso.")
        print(f"[turno 1] {a1[:140]}\n")
        a2 = _turn(client, token, session_id,
                   "Qual é o nome do meu projeto e em que área ele atua?")
        print(f"[turno 2 (mesma session, history=[])] {a2[:200]}\n")

    remembered = any(k in a2.lower() for k in ("zephyr", "eólica", "eolica"))
    print(f"[MEMÓRIA HTTP via thread-por-sessão] {'OK' if remembered else 'FALHOU'}")
    print(f"=== SMOKE HTTP {'PASS' if remembered else 'FAIL'} ===")
    return 0 if remembered else 1


if __name__ == "__main__":
    sys.exit(main())
