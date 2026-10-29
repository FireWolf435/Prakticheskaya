from __future__ import annotations

import os
import time

import psycopg


def main() -> None:
    dsn = os.environ["DATABASE_URL"]
    while True:
        with psycopg.connect(dsn) as conn:
            rows = conn.execute(
                "UPDATE core.outbox SET processed_at = now(), attempts = attempts + 1 "
                "WHERE processed_at IS NULL RETURNING outbox_id"
            ).fetchall()
            if rows:
                print(f"processed outbox events: {len(rows)}", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    main()
