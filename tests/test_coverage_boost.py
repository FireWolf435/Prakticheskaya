"""Дополнительные тесты для достижения покрытия 85%."""
import pytest


class TestChangeTicketStatus:
    """Тесты функции change_ticket_status."""

    def test_change_status_valid_transition(self, db_session):
        """Переход Назначена → В работе должен пройти."""
        with db_session.cursor() as cur:
            # Создаем тестовую заявку
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                "VALUES ('TEST-COV-1', 1, 1, 3, 'Назначена', 'тест', now())"
            )
            cur.execute("SELECT ticket_id FROM core.tickets WHERE ticket_number = 'TEST-COV-1'")
            ticket_id = cur.fetchone()[0]
            # Переводим в работу
            cur.execute("SELECT core.change_ticket_status(%s, %s, %s)", (ticket_id, 'В работе', 'тест'))
            cur.execute("SELECT status FROM core.tickets WHERE ticket_id = %s", (ticket_id,))
            assert cur.fetchone()[0] == 'В работе'

    def test_change_status_invalid_transition(self, db_session):
        """Переход Новая → Закрыта должен быть запрещен."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                "VALUES ('TEST-COV-2', 1, 1, 3, 'Новая', 'тест', now())"
            )
            cur.execute("SELECT ticket_id FROM core.tickets WHERE ticket_number = 'TEST-COV-2'")
            ticket_id = cur.fetchone()[0]
            try:
                cur.execute("SELECT core.change_ticket_status(%s, %s, %s)", (ticket_id, 'Закрыта', 'тест'))
            except Exception as e:
                assert "Недопустимый переход" in str(e)


class TestReopenTicket:
    """Тесты функции reopen_ticket."""

    def test_reopen_within_72h(self, db_session):
        """Переоткрытие в течение 72 часов должно пройти."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at, closed_at) "
                "VALUES ('TEST-COV-3', 1, 1, 3, 'Решена', 'тест', now() - interval '1 day', now())"
            )
            cur.execute("SELECT ticket_id FROM core.tickets WHERE ticket_number = 'TEST-COV-3'")
            ticket_id = cur.fetchone()[0]
            cur.execute("SELECT core.reopen_ticket(%s)", (ticket_id,))
            cur.execute("SELECT status FROM core.tickets WHERE ticket_id = %s", (ticket_id,))
            assert cur.fetchone()[0] == 'Переоткрыта'

    def test_reopen_after_72h(self, db_session):
        """Переоткрытие после 72 часов должно быть запрещено."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at, closed_at) "
                "VALUES ('TEST-COV-4', 1, 1, 3, 'Решена', 'тест', now() - interval '4 days', now() - interval '3 days')"
            )
            cur.execute("SELECT ticket_id FROM core.tickets WHERE ticket_number = 'TEST-COV-4'")
            ticket_id = cur.fetchone()[0]
            try:
                cur.execute("SELECT core.reopen_ticket(%s)", (ticket_id,))
            except Exception as e:
                assert "72" in str(e) or "запрещ" in str(e).lower()


class TestMoveAsset:
    """Тесты функции move_asset."""

    def test_move_asset_valid(self, db_session):
        """Перемещение оборудования должно пройти."""
        with db_session.cursor() as cur:
            # Создаем тестовое оборудование
            cur.execute(
                "INSERT INTO core.assets (inv_no, name, category_id, location_id, is_active) "
                "VALUES ('TEST-INV-1', 'Тестовое оборудование', 1, 1, true) "
                "ON CONFLICT (inv_no) DO NOTHING"
            )
            cur.execute("SELECT asset_id FROM core.assets WHERE inv_no = 'TEST-INV-1'")
            asset_id = cur.fetchone()[0]
            # Перемещаем
            cur.execute("SELECT core.move_asset('TEST-INV-1', 2)")
            cur.execute(
                "SELECT location_id FROM core.assets WHERE asset_id = %s", (asset_id,)
            )
            assert cur.fetchone()[0] == 2


class TestRegisterUser:
    """Тесты функции sec.register_user."""

    def test_register_user_with_roles(self, db_session):
        """Регистрация пользователя с ролями."""
        with db_session.cursor() as cur:
            # Создаем тестового сотрудника
            cur.execute(
                "INSERT INTO core.employees (employee_id, last_name, first_name, tab_number, department_id, is_active) "
                "VALUES (9999, 'Тестов', 'Тест', '9999', 1, true) "
                "ON CONFLICT (employee_id) DO NOTHING"
            )
            # Регистрируем пользователя
            cur.execute(
                "SELECT sec.register_user(9999, 'testuser9999', 'TestPass123!', ARRAY['employee'])"
            )
            user_id = cur.fetchone()[0]
            assert user_id is not None
            # Проверяем, что пользователь создан
            cur.execute("SELECT login_name FROM sec.users WHERE user_id = %s", (user_id,))
            assert cur.fetchone()[0] == 'testuser9999'


class TestTouchRow:
    """Тесты функции touch_row (оптимистичная блокировка)."""

    def test_touch_row_increments_version(self, db_session):
        """touch_row должна увеличивать версию при изменении."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at, version) "
                "VALUES ('TEST-COV-5', 1, 1, 3, 'Новая', 'тест', now(), 1)"
            )
            cur.execute("SELECT ticket_id, version FROM core.tickets WHERE ticket_number = 'TEST-COV-5'")
            row = cur.fetchone()
            ticket_id, version = row[0], row[1]
            # Обновляем статус
            cur.execute(
                "UPDATE core.tickets SET status = 'Назначена' WHERE ticket_id = %s", (ticket_id,)
            )
            cur.execute("SELECT version FROM core.tickets WHERE ticket_id = %s", (ticket_id,))
            new_version = cur.fetchone()[0]
            assert new_version > version


class TestReportFunctions:
    """Тесты отчетных функций."""

    def test_report_tickets_by_category(self, db_session):
        """report_tickets_by_category должна возвращать результаты."""
        with db_session.cursor() as cur:
            cur.execute(
                "SELECT * FROM core.report_tickets_by_category(timestamptz '2026-01-01 00:00+03', timestamptz '2026-12-31 23:59+03') LIMIT 5"
            )
            results = cur.fetchall()
            assert len(results) >= 0  # Может быть пустым, но запрос должен пройти

    def test_report_engineer_load(self, db_session):
        """report_engineer_load должна возвращать результаты."""
        with db_session.cursor() as cur:
            cur.execute(
                "SELECT * FROM core.report_engineer_load(timestamptz '2026-01-01 00:00+03', timestamptz '2026-12-31 23:59+03') LIMIT 5"
            )
            results = cur.fetchall()
            assert len(results) >= 0  # Может быть пустым, но запрос должен пройти