"""Интеграционные тесты (батч 8: 2 теста)."""
import pytest


class TestIntegrationBatch8:
    """Батч 8 интеграционных тестов (последний)."""

    def test_soft_delete(self, db_session):
        """Мягкое удаление."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at, is_deleted) "
                "VALUES ('TEST-B8-1', 1, 1, 3, 'Новая', 'Тест удаление', now(), false) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            cur.execute("UPDATE core.tickets SET is_deleted = true WHERE ticket_id = %s", (ticket_id,))
            cur.execute("SELECT is_deleted FROM core.tickets WHERE ticket_id = %s", (ticket_id,))
            assert cur.fetchone()[0] is True

    def test_asset_creation(self, db_session):
        """Создание оборудования."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.assets (inv_no, name, category_id, location_id, is_active) "
                "VALUES ('TEST-ASSET-8', 'Тестовое оборудование', 1, 1, true) "
                "ON CONFLICT (inv_no) DO NOTHING RETURNING asset_id")
            result = cur.fetchone()
            if result:
                assert result[0] is not None