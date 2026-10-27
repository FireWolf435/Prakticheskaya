"""Интеграционные тесты (батч 3: 2 теста)."""
import pytest


class TestIntegrationBatch3:
    """Батч 3 интеграционных тестов."""

    def test_comment_addition(self, db_session):
        """Добавление комментария к заявке."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                "VALUES ('TEST-B3-1', 1, 1, 3, 'Новая', 'Тест коммент', now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO core.ticket_comments (ticket_id, author_id, body, is_internal, source, created_at, updated_at) "
                "VALUES (%s, 1, 'Тестовый комментарий', FALSE, 'test', now(), now()) RETURNING comment_id",
                (ticket_id,))
            comment_id = cur.fetchone()[0]
            assert comment_id is not None

    def test_attachment_upload(self, db_session):
        """Добавление вложения к заявке."""
        with db_session.cursor() as cur:
            cur.execute(
                "INSERT INTO core.tickets (ticket_number, author_id, category_id, priority, status, subject, created_at) "
                "VALUES ('TEST-B3-2', 1, 1, 3, 'Новая', 'Тест вложение', now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO core.ticket_attachments (ticket_id, file_name, file_path, file_size, mime_type, uploaded_at) "
                "VALUES (%s, 'test.txt', '/tmp/test.txt', 100, 'text/plain', now()) RETURNING attachment_id",
                (ticket_id,))
            attachment_id = cur.fetchone()[0]
            assert attachment_id is not None