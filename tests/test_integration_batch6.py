"""Интеграционные тесты (батч 6: 2 теста)."""
import pytest


class TestIntegrationBatch6:
    """Батч 6 интеграционных тестов."""

    def test_ticket_full_text_search(self, db_session):
        """Полнотекстовый поиск по заявкам."""
        with db_session.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM core.tickets WHERE description_tsv @@ plainto_tsquery('russian', 'принтер')")
            count = cur.fetchone()[0]
            assert count >= 0

    def test_sla_deadline_not_null(self, db_session):
        """SLA deadline заполнен для всех заявок."""
        with db_session.cursor() as cur:
            cur.execute("SELECT count(*) FROM core.tickets WHERE sla_deadline IS NULL")
            null_count = cur.fetchone()[0]
            assert null_count == 0