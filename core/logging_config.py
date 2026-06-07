"""Setup central de logging estruturado + ponto de integração Sentry.

Chame `setup_logging()` uma vez no startup (API e worker). Controlado por env:
  LOG_LEVEL   nível raiz (default INFO)
  LOG_FORMAT  "json" (prod, p/ coletor) ou "text" (dev, default)
  SENTRY_DSN  se presente, ativa Sentry (sentry-sdk é dep declarada)

`request_id` é propagado via contextvar e incluído em todo log record, para
correlacionar logs de uma mesma request (ver middleware em backend/api.py).
"""
import json
import logging
import os
import sys
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_TEXT_FMT = "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s"


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    use_json = os.getenv("LOG_FORMAT", "text").lower() == "json"

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(_JsonFormatter() if use_json else logging.Formatter(_TEXT_FMT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Reduz ruído de libs de I/O em INFO.
    for noisy in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _init_sentry()


def _init_sentry() -> None:
    """Ativa Sentry se SENTRY_DSN estiver setado.

    `sentry-sdk` é dependência declarada (pyproject) → na imagem de prod basta
    setar SENTRY_DSN para ligar. O try/except cobre ambientes onde o SDK não
    está instalado (ex.: suíte local sem reinstalar deps) — degrada para no-op.
    """
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger(__name__).warning(
            "SENTRY_DSN definido mas sentry-sdk não instalado — pulando init. "
            "Rode `pip install sentry-sdk` para ativar."
        )
        return
    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("ENV", "development"),
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
    )
    logging.getLogger(__name__).info("Sentry inicializado")
