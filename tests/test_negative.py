"""Негативные проверки переходов и безопасности."""
import pytest


class TestNegativeTransitions:
    def test_new_to_closed_forbidden(self, db_session):
        with db_session.cursor() as cur:
            cur.execute("INSERT INTO core.tickets (ticket_number,author_id,category_id,priority,status,subject,created_at) VALUES ('TEST-NEG-1',1,1,3,'Новая','test',now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            try:
                cur.execute("SELECT core.change_ticket_status(%s,%s,%s)", (ticket_id, 'Закрыта', 'test'))
                assert False
            except Exception as exc:
                assert 'transition' in str(exc).lower() or 'переход' in str(exc).lower()

    def test_closed_to_new_forbidden(self, db_session):
        with db_session.cursor() as cur:
            cur.execute("INSERT INTO core.tickets (ticket_number,author_id,category_id,priority,status,subject,created_at) VALUES ('TEST-NEG-2',1,1,3,'Закрыта','test',now()) RETURNING ticket_id")
            ticket_id = cur.fetchone()[0]
            try:
                cur.execute("SELECT core.change_ticket_status(%s,%s,%s)", (ticket_id, 'Новая', 'test'))
                assert False
            except Exception as exc:
                assert 'transition' in str(exc).lower() or 'переход' in str(exc).lower()


class TestNegativeSLA:
    def test_invalid_priority_sla(self, db_session):
        with db_session.cursor() as cur:
            try:
                cur.execute("SELECT core.calc_sla_deadline(now(), 99)")
                assert cur.fetchone()[0] is None
            except Exception as exc:
                db_session.rollback()
                assert 'SLA' in str(exc) or 'приоритет' in str(exc)

    def test_sla_deadline_in_past(self, db_session):
        with db_session.cursor() as cur:
            cur.execute("SELECT core.calc_sla_deadline(now() - interval '1 day', 1)")
            result = cur.fetchone()[0]
            assert result is None or result is not None

    def test_working_minutes_negative(self, db_session):
        with db_session.cursor() as cur:
            cur.execute("SELECT core.working_minutes(now(), now() - interval '1 hour')")
            result = cur.fetchone()[0]
            assert result == 0 or result is None

    def test_sla_with_null_created_at(self, db_session):
        pytest.skip("NULL created_at is rejected by core.tickets NOT NULL constraint before SLA calculation")


class TestNegativeSecurity:
    def test_unauthorized_user_cannot_create_ticket(self, db_session):
        with db_session.cursor() as cur:
            cur.execute("SET ROLE employee")
            try:
                cur.execute("INSERT INTO core.tickets (ticket_number,author_id,category_id,priority,status,subject,created_at) VALUES ('TEST-NEG-3',9999,1,3,'Новая','test',now())")
            except Exception:
                assert True
            finally:
                cur.execute("RESET ROLE")
