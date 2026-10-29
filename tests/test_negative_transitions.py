"""Негативные тесты для переходов статусов (4 теста)."""
import pytest


class TestNegativeTransitions:
    """Негативные тесты для переходов статусов."""

    def test_new_to_closed_forbidden(self, db_session):
        """Переход Новая → Закрыта должен быть запрещен."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                "VALUES ('TEST-NEG-TR-1', 1, 1, 3, 'Новая', 'тест', now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            try:
                cur.execute("SELECT core.change_ticket_status(%s, %s, %s)", (ticket_id, 'Закрыта', 'попытка'))
                assert False, "Should have raised exception"
            except Exception as e:
                assert "Недопустимый переход" in str(e)

    def test_closed_to_new_forbidden(self, db_session):
        """Переход Закрыта → Новая должен быть запрещен."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                "VALUES ('TEST-NEG-TR-2', 1, 1, 3, 'Закрыта', 'тест', now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            try:
                cur.execute("SELECT core.change_ticket_status(%s, %s, %s)", (ticket_id, 'Новая', 'попытка'))
                assert False, "Should have raised exception"
            except Exception as e:
                assert "Недопустимый переход" in str(e)

    def test_resolved_to_in_progress_forbidden(self, db_session):
        """Переход Решена → В работе должен быть запрещен (только Переоткрыта)."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                "VALUES ('TEST-NEG-TR-3', 1, 1, 3, 'Решена', 'тест', now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            try:
                cur.execute("SELECT core.change_ticket_status(%s, %s, %s)", (ticket_id, 'В работе', 'попытка'))
                assert False, "Should have raised exception"
            except Exception as e:
                assert "Недопустимый переход" in str(e)

    def test_reopen_after_72h_forbidden(self, db_session):
        """Переоткрытие после 72 часов должно быть запрещено."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at, closed_at) "
                "VALUES ('TEST-NEG-TR-4', 1, 1, 3, 'Решена', 'тест', now() - interval '4 days', now() - interval '3 days') RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO core.ticket_status_history (ticket_id, to_status, changed_at) "
                "VALUES (%s, 'Решена', now() - interval '3 days')",
                (ticket_id,),
            )
            try:
                cur.execute("SELECT core.reopen_ticket(%s)", (ticket_id,))
                assert False, "Should have raised exception"
            except Exception as e:
                assert "72" in str(e) or "запрещ" in str(e).lower()
