from ._db import connect


def calc_sla_deadline(start: str, priority: int):
    with connect() as conn:
        return conn.execute("SELECT core.calc_sla_deadline(%s::timestamptz, %s)", (start, priority)).fetchone()[0]
