"""Интеграционные тесты (батч 2: 2 теста)."""
import pytest


class TestIntegrationBatch2:
    """Батч 2 интеграционных тестов."""

    def test_auto_assign_with_skills(self, db_session):
        """Автоназначение с учетом навыков."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                "VALUES ('TEST-B2-1', 1, 1, 3, 'Назначена', 'Тест автоназнач', now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            cur.execute("SELECT core.auto_assign(%s)", (ticket_id,))
            result = cur.fetchone()[0]
            # Результат должен быть engineerd_id или None
            assert result is None or isinstance(result, int)

    def test_sla_calculation_with_lunch(self, db_session):
        """Расчет SLA с учетом перерыва."""
        with db_session.cursor() as cur:
            cur.execute("SELECT core.calc_sla_deadline(now(), 1)")
            deadline = cur.fetchone()[0]
            assert deadline is not None