-- ============================================================================
-- Схема audit — журналы аудита
-- audit.log секционирована по месяцам (производительность на больших объемах)
-- ============================================================================

-- Расширение pgcrypto для работы с хешами/шифрованием при необходимости
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- --------------------------------------------------------
-- Родительская таблица аудита (секционирование по месяцам)
-- --------------------------------------------------------
CREATE TABLE audit.log (
    log_id          BIGSERIAL,
    schema_name     VARCHAR(100) NOT NULL,
    table_name      VARCHAR(100) NOT NULL,
    operation       VARCHAR(10) NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')),
    row_id          TEXT,                        -- идентификатор изменённой строки
    old_values      JSONB,                       -- jsonb-дифф «было»
    new_values      JSONB,                       -- jsonb-дифф «стало»
    changed_by      INTEGER REFERENCES core.employees(employee_id) ON DELETE SET NULL,
    changed_by_role NAME,                        -- роль, от имени которой внесено изменение
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    session_id      BIGINT,                      -- pg_backend_pid() на момент изменения
    query_id        BIGINT,                      -- идентификатор запроса (если доступен)
    PRIMARY KEY (log_id, changed_at)
) PARTITION BY RANGE (changed_at);

COMMENT ON TABLE audit.log IS 'Журнал аудита изменений (jsonb-дифф было/стало, кто и когда). Секционировано по месяцам.';
COMMENT ON COLUMN audit.log.old_values IS 'JSONB-дифф старых значений полей';
COMMENT ON COLUMN audit.log.new_values IS 'JSONB-дифф новых значений полей';
COMMENT ON COLUMN audit.log.changed_by_role IS 'Роль БД, от имени которой выполнено изменение';

-- --------------------------------------------------------
-- Секции по месяцам (примеры для 2026 года, будут создаваться автоматически триггером)
-- --------------------------------------------------------
CREATE TABLE audit.log_y2026m10 PARTITION OF audit.log
FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');

CREATE TABLE audit.log_y2026m11 PARTITION OF audit.log
FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');

CREATE TABLE audit.log_y2026m12 PARTITION OF audit.log
FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

-- DEFAULT-секция: сюда попадут записи за даты вне объявленных диапазонов,
-- чтобы вставка в журнал аудита не завершалась ошибкой.
CREATE TABLE audit.log_default PARTITION OF audit.log DEFAULT;

-- Индексы для ускорения поиска в аудите
CREATE INDEX idx_audit_log_table_time ON audit.log (table_name, changed_at);
CREATE INDEX idx_audit_log_user_time ON audit.log (changed_by, changed_at);

-- --------------------------------------------------------
-- Журнал событий безопасности (входы, блокировки, отказы доступа)
-- --------------------------------------------------------
CREATE TABLE audit.security_events (
    event_id        BIGSERIAL PRIMARY KEY,
    event_type      VARCHAR(50) NOT NULL,  -- login_success, login_failed, nologin, role_change, ddl_event
    user_name       NAME,
    role_name       NAME,
    source_ip       INET,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    details         JSONB
);

COMMENT ON TABLE audit.security_events IS 'Журнал событий безопасности (входы, блокировки, изменения ролей)';

CREATE INDEX idx_security_events_time ON audit.security_events (occurred_at);
CREATE INDEX idx_security_events_user ON audit.security_events (user_name, occurred_at);

-- --------------------------------------------------------
-- Журнал DDL-событий (создаётся событийным триггером)
-- --------------------------------------------------------
CREATE TABLE audit.ddl_log (
    ddl_id          BIGSERIAL PRIMARY KEY,
    event           TEXT NOT NULL,           -- CREATE, ALTER, DROP, GRANT, REVOKE
    object_type     TEXT NOT NULL,
    object_name     TEXT NOT NULL,
    schema_name     TEXT NOT NULL,
    executed_by     NAME NOT NULL,
    executed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sql_command     TEXT,
    tags            TEXT[]
);

COMMENT ON TABLE audit.ddl_log IS 'Журнал DDL-событий (событийный триггер: CREATE, ALTER, DROP, GRANT, REVOKE)';
CREATE INDEX idx_ddl_log_executed_at ON audit.ddl_log (executed_at);
