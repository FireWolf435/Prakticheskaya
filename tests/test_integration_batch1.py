"""Интеграционные тесты (батч 1: 2 теста)."""
import pytest


class TestIntegrationBatch1:
    """Батч 1 интеграционных тестов."""

    def test_ticket_creation_with_sla(self, db_session):
        """Создание заявки с автоматическим расчетом SLA."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                "VALUES ('TEST-B1-1', 1, 1, 1, 'Новая', 'Тест SLA', now()) RETURNING ticket_id, sla_deadline")
            row = cur.fetchone()
            assert row[0] is not None
            assert row[1] is not None  # sla_deadline заполнен

    def test_ticket_status_transition_with_history(self, db_session):
        """Переход статуса с записью в историю."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                "VALUES ('TEST-B1-2', 1, 1, 3, 'Новая', 'Тест переход', now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            cur.execute("SELECT core.change_ticket_status(%s, %s, %s)", (ticket_id, 'Назначена', 'тест'))
            cur.execute("SELECT count(*) FROM core.ticket_status_history WHERE ticket_id = %s", (ticket_id,))
            assert cur.fetchone()[0] >= 1