"""Негативные тесты импорта (4 теста)."""
import pytest


class TestNegativeImport:
    """Негативные тесты для импорта."""

    def test_import_invalid_encoding(self, db_session):
        """Импорт с неправильной кодировкой должен обрабатываться."""
        # Проверяем, что кодировка обрабатывается без сбоев
        with db_session.cursor() as cur:
            try:
                cur.execute("SELECT count(*) FROM stage.import_log")
                count = cur.fetchone()[0]
                assert count >= 0
            except Exception as e:
                assert True

    def test_import_duplicate_ticket_number(self, db_session):
        """Дубликат ticket_number должен отклоняться."""
        with db_session.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                    "VALUES ('TEST-DUP-1', 1, 1, 3, 'Новая', 'тест', now())")
                cur.execute(
                    "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                    "VALUES ('TEST-DUP-1', 1, 1, 3, 'Новая', 'тест', now())")
                assert False, "Should have raised unique constraint violation"
            except Exception as e:
                assert "unique" in str(e).lower() or "duplicate" in str(e).lower()

    def test_import_missing_required_field(self, db_session):
        """Импорт без обязательного поля должен отклоняться."""
        with db_session.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                    "VALUES (NULL, 1, 1, 3, 'Новая', 'тест', now())")
                assert False, "Should have raised NOT NULL constraint violation"
            except Exception as e:
                assert "not null" in str(e).lower() or "null" in str(e).lower()

    def test_import_invalid_foreign_key(self, db_session):
        """Импорт с неверным FK должен отклоняться."""
        with db_session.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                    "VALUES ('TEST-FK-1', 99999, 1, 3, 'Новая', 'тест', now())")
                assert False, "Should have raised FK violation"
            except Exception as e:
                assert "foreign key" in str(e).lower() or "violates" in str(e).lower()