import os
from pathlib import Path
import psycopg


def connect() -> psycopg.Connection:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        values: dict[str, str] = {}
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip('"').strip("'")
        if values.get("POSTGRES_USER") and values.get("POSTGRES_PASSWORD") and values.get("POSTGRES_DB"):
            dsn = (
                f"host=localhost port={values.get('POSTGRES_PORT', '5432')} "
                f"dbname={values['POSTGRES_DB']} user={values['POSTGRES_USER']} "
                f"password={values['POSTGRES_PASSWORD']}"
            )
    if not dsn:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg.connect(dsn)
