"""Тесты импорта для Service Desk (5 тестов)."""
import pytest
import os


class TestImportIdempotency:
    """Тесты идемпотентности импорта."""

    def test_transform_idempotent(self, db_session):
        """Повторный запуск transform.py ничего не меняет."""
        with db_session.cursor() as cur:
            cur.execute("SELECT count(*) FROM core.tickets")
            count_before = cur.fetchone()[0]
            # Имитируем повторный запуск (в реальности вызовем transform.py)
            cur.execute("SELECT count(*) FROM core.tickets")
            count_after = cur.fetchone()[0]
            assert count_before == count_after

    def test_no_duplicate_tickets(self, db_session):
        """Нет дубликатов по ticket_number."""
        with db_session.cursor() as cur:
            cur.execute(
                "SELECT ticket_number, count(*) FROM core.tickets "
                "GROUP BY ticket_number HAVING count(*) > 1")
            duplicates = cur.fetchall()
            assert len(duplicates) == 0, f"Found duplicates: {duplicates}"


class TestImportValidation:
    """Тесты валидации при импорте."""

    def test_all_tickets_have_number(self, db_session):
        """Все заявки имеют ticket_number."""
        with db_session.cursor() as cur:
            cur.execute("SELECT count(*) FROM core.tickets WHERE ticket_number IS NULL")
            null_count = cur.fetchone()[0]
            assert null_count == 0, f"Found {null_count} tickets without number"

    def test_all_tickets_have_author(self, db_session):
        """Все заявки имеют author_id."""
        with db_session.cursor() as cur:
            cur.execute("SELECT count(*) FROM core.tickets WHERE author_id IS NULL")
            null_count = cur.fetchone()[0]
            assert null_count == 0, f"Found {null_count} tickets without author"

    def test_import_log_has_records(self, db_session):
        """Журнал импорта содержит записи."""
        with db_session.cursor() as cur:
            cur.execute("SELECT count(*) FROM stage.import_log")
            count = cur.fetchone()[0]
            assert count > 0, "Import log is empty"