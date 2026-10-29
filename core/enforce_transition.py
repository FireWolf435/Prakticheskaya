from ._db import connect


def check_transition(ticket_id: int, old_status: str, new_status: str) -> bool:
    with connect() as conn:
        try:
            current = conn.execute("SELECT status::text FROM core.tickets WHERE ticket_id = %s", (ticket_id,)).fetchone()
            if current is None or current[0] != old_status:
                return False
            conn.execute("SELECT core.change_ticket_status(%s, %s, %s)", (ticket_id, new_status, "test"))
            return True
        except Exception:
            return False
