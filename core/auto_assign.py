from ._db import connect


def auto_assign(ticket_id: int):
    with connect() as conn:
        return conn.execute("SELECT core.auto_assign(%s)", (ticket_id,)).fetchone()[0]
