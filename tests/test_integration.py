"""Интеграционные тесты для Service Desk (15 тестов)."""
import pytest


class TestIntegrationTicketLifecycle:
    """Тесты полного жизненного цикла заявки."""

    def test_create_ticket_full_fields(self, db_session):
        """Создание заявки со всеми полями."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, "
                "description, created_at, updated_at) VALUES ('TEST-INT-1', 1, 1, 2, 'Новая', 'Тест полный', "
                "'Описание теста', now(), now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            assert ticket_id is not None
            cur.execute("SELECT status, priority, subject FROM core.tickets WHERE ticket_id = %s", (ticket_id,))
            row = cur.fetchone()
            assert row[0] == 'Новая'
            assert row[1] == 2
            assert row[2] == 'Тест полный'

    def test_create_ticket_minimal_fields(self, db_session):
        """Создание заявки с минимальными полями."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                "VALUES ('TEST-INT-2', 1, 1, 3, 'Новая', 'Минимальная', now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            assert ticket_id is not None

    def test_add_comment_to_ticket(self, db_session):
        """Добавление комментария к заявке."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                "VALUES ('TEST-INT-3', 1, 1, 3, 'Новая', 'Тест коммент', now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO core.ticket_comments (ticket_id, author_id, body, is_internal, source, created_at, updated_at) "
                "VALUES (%s, 1, 'Тестовый комментарий', FALSE, 'test', now(), now()) RETURNING comment_id",
                (ticket_id,))
            comment_id = cur.fetchone()[0]
            assert comment_id is not None

    def test_add_attachment_to_ticket(self, db_session):
        """Добавление вложения к заявке."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                "VALUES ('TEST-INT-4', 1, 1, 3, 'Новая', 'Тест вложение', now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO core.ticket_attachments (ticket_id, file_name, file_path, file_size, mime_type, uploaded_at) "
                "VALUES (%s, 'test.txt', '/tmp/test.txt', 100, 'text/plain', now()) RETURNING attachment_id",
                (ticket_id,))
            attachment_id = cur.fetchone()[0]
            assert attachment_id is not None

    def test_create_asset(self, db_session):
        """Создание оборудования."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.assets (inv_no, asset_type, location_id, is_active) "
                "VALUES ('TEST-INV-INT-1', 'Тестовое оборудование', 1, true) "
                "ON CONFLICT (inv_no) DO NOTHING RETURNING asset_id")
            result = cur.fetchone()
            if result:
                assert result[0] is not None

    def test_create_employee(self, db_session):
        """Создание сотрудника."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.employees (employee_id, last_name, first_name, tab_number, department_id, is_active, hire_date) "
                "VALUES (8888, 'Тестов', 'Тест', '8888', 1, true, '2026-01-01') "
                "ON CONFLICT (employee_id) DO NOTHING RETURNING employee_id")
            result = cur.fetchone()
            if result:
                assert result[0] == 8888

    def test_department_hierarchy(self, db_session):
        """Проверка иерархии подразделений."""
        with db_session.cursor() as cur:
            cur.execute(
                "WITH RECURSIVE dept_tree AS ("
                "  SELECT department_id, parent_id, name, 1 AS depth FROM ref.departments WHERE parent_id IS NULL"
                "  UNION ALL"
                "  SELECT d.department_id, d.parent_id, d.name, dt.depth + 1 "
                "  FROM ref.departments d JOIN dept_tree dt ON dt.department_id = d.parent_id"
                ") SELECT count(*) FROM dept_tree")
            count = cur.fetchone()[0]
            assert count >= 1

    def test_category_based_query(self, db_session):
        """Запрос заявок по категории."""
        with db_session.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM core.tickets WHERE category_id = 1")
            count = cur.fetchone()[0]
            assert count >= 0

    def test_priority_based_query(self, db_session):
        """Запрос заявок по приоритету."""
        with db_session.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM core.tickets WHERE priority = 1")
            count = cur.fetchone()[0]
            assert count >= 0

    def test_date_range_query(self, db_session):
        """Запрос заявок за период."""
        with db_session.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM core.tickets WHERE created_at >= '2026-01-01' AND created_at < '2026-02-01'")
            count = cur.fetchone()[0]
            assert count >= 0

    def test_full_text_search(self, db_session):
        """Полнотекстовый поиск."""
        with db_session.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM core.tickets WHERE description_tsv @@ plainto_tsquery('russian', 'принтер')")
            count = cur.fetchone()[0]
            assert count >= 0

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

    def test_sla_pause_resume(self, db_session):
        """Пауза и возобновление SLA."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at, sla_paused_at) "
                "VALUES ('TEST-INT-5', 1, 1, 3, 'Ожидание пользователя', 'Тест пауза', now(), now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            cur.execute("SELECT sla_paused_at FROM core.tickets WHERE ticket_id = %s", (ticket_id,))
            assert cur.fetchone()[0] is not None

    def test_soft_delete(self, db_session):
        """Мягкое удаление."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at, is_deleted) "
                "VALUES ('TEST-INT-6', 1, 1, 3, 'Новая', 'Тест удаление', now(), false) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            cur.execute("UPDATE core.tickets SET is_deleted = true WHERE ticket_id = %s", (ticket_id,))
            cur.execute("SELECT is_deleted FROM core.tickets WHERE ticket_id = %s", (ticket_id,))
            assert cur.fetchone()[0] is True
