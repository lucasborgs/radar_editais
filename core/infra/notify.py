"""Alertas operacionais por e-mail (SMTP) — spec hardening-pre-beta PR4.3.

Canal único de notificação para falhas de background (crons do worker). Decisão
de produto (2026-07-01): Gmail app-password via SMTP/STARTTLS — sem dependência
nova, sem provedor transacional.

Contrato de resiliência:
  - Sem `ALERT_SMTP_USER`/`ALERT_EMAIL_TO` configurados → no-op (loga warning
    UMA vez por processo) — dev/local não quebra nem enche o log.
  - Falha de envio → loga erro e retorna False, NUNCA propaga: um alerta jamais
    pode derrubar o cron que ele deveria observar.

Env vars (ver .env.example):
  ALERT_SMTP_HOST      default smtp.gmail.com
  ALERT_SMTP_PORT      default 587 (STARTTLS)
  ALERT_SMTP_USER      conta SMTP (Gmail: o próprio e-mail)
  ALERT_SMTP_PASSWORD  Gmail: app password (não a senha da conta)
  ALERT_EMAIL_FROM     default = ALERT_SMTP_USER
  ALERT_EMAIL_TO       destinatário dos alertas
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)

# Warning de "não configurado" só uma vez por processo — o worker chama
# send_alert a cada run de cron; sem isto o log diário viraria ruído.
_warned_unconfigured = False


def send_alert(subject: str, body: str) -> bool:
    """Envia um e-mail de alerta operacional. Retorna True se enviado.

    No-op (False) sem configuração; False também em falha de envio — nunca
    levanta (ver contrato no docstring do módulo).
    """
    global _warned_unconfigured

    user = (os.getenv("ALERT_SMTP_USER") or "").strip()
    to = (os.getenv("ALERT_EMAIL_TO") or "").strip()
    if not user or not to:
        if not _warned_unconfigured:
            logger.warning(
                "send_alert: ALERT_SMTP_USER/ALERT_EMAIL_TO não configurados — "
                "alertas por e-mail desativados (no-op)"
            )
            _warned_unconfigured = True
        return False

    host = (os.getenv("ALERT_SMTP_HOST") or "smtp.gmail.com").strip()
    try:
        port = int(os.getenv("ALERT_SMTP_PORT") or 587)
    except ValueError:
        logger.error("send_alert: ALERT_SMTP_PORT inválida — usando 587")
        port = 587
    password = os.getenv("ALERT_SMTP_PASSWORD") or ""
    sender = (os.getenv("ALERT_EMAIL_FROM") or "").strip() or user

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        logger.info("send_alert: alerta enviado para %s (%r)", to, subject)
        return True
    except Exception as e:
        # Nunca propaga — alerta não pode derrubar o cron que o dispara.
        logger.error("send_alert: falha ao enviar %r: %s", subject, e)
        return False
