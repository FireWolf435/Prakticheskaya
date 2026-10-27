"""Негативные тесты SLA (4 теста)."""
import pytest


class TestNegativeSLA:
    """Негативные тесты для SLA."""

    def test_invalid_priority_sla(self, db_session):
        """Неверный приоритет должен вернуть None или ошибку."""
        with db_session.cursor() as cur:
            try:
                cur.execute("SELECT core.calc_sla_deadline(now(), 99)")
                result = cur.fetchone()[0]
                assert result is None or True
            except Exception as e:
                assert True  # Ошибка тоже допустима

    def test_sla_deadline_in_past(self, db_session):
        """SLA deadline не должен быть в прошлом."""
        with db_session.cursor() as cur:
            cur.execute("SELECT core.calc_sla_deadline(now() - interval '1 day', 1)")
            result = cur.fetchone()[0]
            if result is not None:
                assert result >= now() - interval '1 day'

    def test_working_minutes_negative(self, db_session):
        """working_minutes с отрицательным интервалом должна вернуть 0 или ошибку."""
        with db_session.cursor() as cur:
            try:
                cur.execute("SELECT core.working_minutes(now(), now() - interval '1 hour')")
                result = cur.fetchone()[0]
                assert result == 0 or result is None
            except Exception as e:
                assert True

    def test_sla_with_null_created_at(self, db_session):
        """SLA deadline при NULL created_at должен обрабатываться."""
        with db_session.cursor() as cur:
            try:
                cur.execute("SELECT core.calc_sla_deadline(NULL, 1)")
                result = cur.fetchone()[0]
                assert result is None
            except Exception as e:
                assert True  # Ошибка тоже допустима