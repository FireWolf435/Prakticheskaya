import pytest
import subprocess
import time

@pytest.fixture(scope="session", autouse=True)
def docker_services():
    """Запуск контейнеров перед тестами и выключение после."""
    # Запуск необходимых контейнеров
    subprocess.run(["docker", "compose", "up", "-d", "postgres", "ldap"], capture_output=True)
    # Ждем, пока PostgreSQL станет готов
    for _ in range(30):
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "postgres", "pg_isready", "-U", "dba_admin", "-d", "servicedesk"],
            capture_output=True, text=True
        )
        if "ready" in result.stdout.lower():
            break
        time.sleep(2)
    yield
    # Остановка контейнеров после тестов
    subprocess.run(["docker", "compose", "down", "-v"], capture_output=True)

@pytest.fixture(scope="function")
def db_session():
    """Получение сессии БД для каждого теста."""
    import psycopg2
    conn = psycopg2.connect(
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