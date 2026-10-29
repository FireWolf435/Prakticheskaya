import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(scope="session", autouse=True)
def docker_services():
    """Проверяет уже запущенную PostgreSQL; жизненным циклом Docker не управляет."""
    yield
    # Контейнеры не удаляем: тестовый запуск не должен уничтожать данные БД.

@pytest.fixture(scope="function")
def db_session():
    """Получение сессии БД для каждого теста."""
    import psycopg
    conn = psycopg.connect(
        host="localhost",
        port=5432,
        user="dba_admin",
        password="118100",
        dbname="servicedesk"
    )
    conn.autocommit = True
    yield conn
    # Очистка после теста: удаляем тестовые данные
    with conn.cursor() as cur:
        cur.execute("DELETE FROM core.tickets WHERE ticket_number LIKE 'TEST_%'")
    conn.close()
