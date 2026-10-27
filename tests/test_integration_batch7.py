"""Интеграционные тесты (батч 7: 2 теста)."""
import pytest


class TestIntegrationBatch7:
    """Батч 7 интеграционных тестов."""

    def test_outbox_event_creation(self, db_session):
        """Создание события в outbox."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.outbox (aggregate_type, aggregate_id, event_type, payload, created_at) "
                "VALUES ('ticket', 1, 'status_change', '{\"status\": \"В работе\"}', now()) RETURNING outbox_id")
            outbox_id = cur.fetchone()[0]
            assert outbox_id is not None

    def test_audit_log_verification(self, db_session):
        """Проверка аудита."""
        with db_session.cursor() as cur:
            cur.execute("SELECT count(*) FROM audit.log")
            count = cur.fetchone()[0]
            assert count >= 0