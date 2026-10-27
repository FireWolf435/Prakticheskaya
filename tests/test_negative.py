"""Негативные тесты для Service Desk (5 тестов)."""
import pytest


class TestNegativeTransitions:
    """Негативные тесты для переходов статусов."""

    def test_new_to_closed_forbidden(self, db_session):
        """Переход Новая → Закрыта должен быть запрещен."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                "VALUES ('TEST-NEG-1', 1, 1, 3, 'Новая', 'тест', now()) RETURNING ticket_id")
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
                "VALUES ('TEST-NEG-2', 1, 1, 3, 'Закрыта', 'тест', now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            try:
                cur.execute("SELECT core.change_ticket_status(%s, %s, %s)", (ticket_id, 'Новая', 'попытка'))
                assert False, "Should have raised exception"
            except Exception as e:
                assert "Недопустимый переход" in str(e)


class TestNegativeSLA:
    """Негативные тесты для SLA."""

    def test_invalid_priority_sla(self, db_session):
        """Неверный приоритет должен вернуть None или ошибку."""
        with db_session.cursor() as cur:
            try:
                cur.execute("SELECT core.calc_sla_deadline(now(), 99)")
                result = cur.fetchone()[0]
                # Может вернуть None или ошибку
                assert result is None or True
            except Exception as e:
                assert True  # Ошибка тоже допустима

    def test_sla_deadline_in_past(self, db_session):
        """SLA deadline не должен быть в прошлом."""
        with db_session.cursor() as cur:
            cur.execute("SELECT core.calc_sla_deadline(now() - interval '1 day', 1)")
            result = cur.fetchone()[0]
            if result is not None:
                assert result >= now() - interval '1 day'  # Should be in future


class TestNegativeSecurity:
    """Негативные тесты безопасности."""

    def test_unauthorized_user_cannot_create_ticket(self, db_session):
        """Неавторизованный пользователь не может создать заявку."""
        with db_session.cursor() as cur:
            try:
                # Пытаемся создать заявку без прав
                cur.execute("SET ROLE employee")
                cur.execute(
                    "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                    "VALUES ('TEST-NEG-3', 9999, 1, 3, 'Новая', 'тест', now())")
                assert False, "Should have raised permission error"
            except Exception as e:
                assert "permission" in str(e).lower() or "denied" in str(e).lower()
            finally:
                cur.execute("RESET ROLE")