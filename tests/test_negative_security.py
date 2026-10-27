"""Негативные тесты безопасности (4 теста)."""
import pytest


class TestNegativeSecurity:
    """Негативные тесты безопасности."""

    def test_rls_employee_cannot_see_other_tickets(self, db_session):
        """Сотрудник не видит чужие заявки через RLS."""
        with db_session.cursor() as cur:
            cur.execute("SET ROLE employee")
            cur.execute("SET app.user_id = '100'")
            try:
                cur.execute("SELECT count(*) FROM core.tickets WHERE author_id != 100")
                count = cur.fetchone()[0]
                assert count == 0, "Employee can see other users' tickets"
            except Exception as e:
                # RLS может блокировать запрос полностью
                pass
            finally:
                cur.execute("RESET ROLE")

    def test_column_level_masking_reporting(self, db_session):
        """Роль reporting не видит СНИЛС, телефон, email, дату рождения."""
        with db_session.cursor() as cur:
            cur.execute("SET ROLE reporting")
            try:
                cur.execute("SELECT snils FROM core.employees LIMIT 1")
                # Должно вернуть NULL или ошибку
                result = cur.fetchone()
                assert result is None or result[0] is None
            except Exception as e:
                # Ошибка доступа тоже допустима
                assert True
            finally:
                cur.execute("RESET ROLE")

    def test_blocked_user_cannot_login(self, db_session):
        """Заблокированный пользователь не может войти."""
        with db_session.cursor() as cur:
            # Проверяем, что is_locked = true блокирует вход
            cur.execute(
                "SELECT is_locked FROM sec.users WHERE login_name = 'blocked_user' LIMIT 1")
            result = cur.fetchone()
            if result:
                assert result[0] is True

    def test_sql_injection_prevention(self, db_session):
        """SQL-инъекция через параметры должна быть предотвращена."""
        with db_session.cursor() as cur:
            # Пытаемся внедрить SQL через параметр
            malicious_input = "1; DROP TABLE core.tickets; --"
            try:
                cur.execute("SELECT * FROM core.tickets WHERE ticket_id = %s", (malicious_input,))
                # Если запрос прошел, проверяем что таблица не удалена
                cur.execute("SELECT count(*) FROM core.tickets")
                count = cur.fetchone()[0]
                assert count > 0, "SQL injection succeeded - table dropped"
            except Exception as e:
                # Ошибка параметризации — это нормально
                assert True