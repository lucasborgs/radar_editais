import logging

from radar.core.infra.logging_config import _RedactionFilter


def test_task_payload_is_redacted_before_handlers_see_it():
    record = logging.LogRecord("worker", logging.INFO, __file__, 1, "task args=%s cnpj=%s", ({"perfil": "secreto"}, "12345678000199"), None)
    _RedactionFilter().filter(record)
    rendered = record.getMessage()
    assert "secreto" not in rendered and "12345678000199" not in rendered
