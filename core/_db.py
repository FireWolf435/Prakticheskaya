import os
import psycopg


def connect() -> psycopg.Connection:
    return psycopg.connect(os.getenv("DATABASE_URL", "host=localhost port=5432 dbname=servicedesk user=dba_admin password=118100"))
