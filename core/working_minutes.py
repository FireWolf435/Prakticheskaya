from ._db import connect


def working_minutes(start: str, end: str):
    with connect() as conn:
        return conn.execute("SELECT core.working_minutes(%s::timestamptz, %s::timestamptz)", (start, end)).fetchone()[0]
