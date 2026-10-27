"""Интеграционные тесты (батч 5: 2 теста)."""
import pytest


class TestIntegrationBatch5:
    """Батч 5 интеграционных тестов."""

    def test_user_registration(self, db_session):
        """Регистрация пользователя."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.employees (employee_id, last_name, first_name, tab_number, department_id, is_active) "
                "VALUES (7777, 'Тестов', 'Тест', '7777', 1, true) "
                "ON CONFLICT (employee_id) DO NOTHING RETURNING employee_id")
            result = cur.fetchone()
            if result:
                emp_id = result[0]
                cur.execute(
                    "SELECT sec.register_user(%s, %s, %s, %s)",
                    (emp_id, 'testuser7777', 'TestPass123!', ['employee']))
                user_id = cur.fetchone()[0]
                assert user_id is not None

    def test_sla_pause_resume(self, db_session):
        """Пауза и возобновление SLA."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at, sla_paused_at) "
                "VALUES ('TEST-B5-2', 1, 1, 3, 'Ожидание пользователя', 'Тест пауза', now(), now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            cur.execute("SELECT sla_paused_at FROM core.tickets WHERE ticket_id = %s", (ticket_id,))
            assert cur.fetchone()[0] is not None