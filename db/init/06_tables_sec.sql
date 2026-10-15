-- ============================================================================
-- Схема sec — безопасность
-- Пользователи, роли, сессии, история паролей, регистрация
-- ============================================================================

-- --------------------------------------------------------
-- Пользователи системы (связь с core.employees и LDAP)
-- --------------------------------------------------------
CREATE TABLE sec.users (
    user_id         SERIAL PRIMARY KEY,
    employee_id     INTEGER NOT NULL REFERENCES core.employees(employee_id) ON DELETE CASCADE,
    login_name      VARCHAR(100) NOT NULL UNIQUE,  -- правило именования: первая буква фамилии + инициалы
    password_hash   TEXT NOT NULL,                  -- SCRAM-SHA-256, хранится вне репозитория
    password_changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until     TIMESTAMPTZ,                    -- срок действия учётной записи
    connection_limit INTEGER NOT NULL DEFAULT 10,    -- лимит соединений
    is_locked       BOOLEAN NOT NULL DEFAULT FALSE,  -- заблокирован (NOLOGIN)
    locked_at       TIMESTAMPTZ,
    locked_reason   VARCHAR(255),
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    last_failed_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      INTEGER REFERENCES core.employees(employee_id) ON DELETE SET NULL,
    CONSTRAINT chk_login_not_empty CHECK (trim(login_name) <> '')
);

COMMENT ON TABLE sec.users IS 'Учётные записи пользователей (сопоставление с сотрудниками по табельному номеру)';
COMMENT ON COLUMN sec.users.login_name IS 'Логин по единому правилу именования (учёты коллизий)';
COMMENT ON COLUMN sec.users.password_hash IS 'Хеш пароля SCRAM-SHA-256 (не хранится в репозитории)';
COMMENT ON COLUMN sec.users.valid_until IS 'Срок действия учётной записи';

-- --------------------------------------------------------
-- Сопоставление пользователей и ролей БД
-- --------------------------------------------------------
CREATE TABLE sec.user_roles (
    user_id    INTEGER NOT NULL REFERENCES sec.users(user_id) ON DELETE CASCADE,
    role_name  NAME NOT NULL,  -- employee, engineer, dispatcher, dept_head, auditor, reporting, app_service, migrator, dba
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    granted_by INTEGER REFERENCES core.employees(employee_id) ON DELETE SET NULL,
    PRIMARY KEY (user_id, role_name)
);

COMMENT ON TABLE sec.user_roles IS 'Сопоставление пользователей и групповых ролей БД';

-- --------------------------------------------------------
-- История паролей (для проверки не reuse предыдущих паролей)
-- --------------------------------------------------------
CREATE TABLE sec.password_history (
    history_id     BIGSERIAL PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES sec.users(user_id) ON DELETE CASCADE,
    password_hash  TEXT NOT NULL,
    changed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    changed_by     INTEGER REFERENCES core.employees(employee_id) ON DELETE SET NULL
);

COMMENT ON TABLE sec.password_history IS 'История паролей пользователя (чтобы не повторялись старые)';
CREATE INDEX idx_password_history_user ON sec.password_history (user_id, changed_at);

-- --------------------------------------------------------
-- Активные сессии (для завершения при блокировке/увольнении)
-- --------------------------------------------------------
CREATE TABLE sec.active_sessions (
    session_id    BIGINT PRIMARY KEY,  -- pg_backend_pid()
    user_id       INTEGER NOT NULL REFERENCES sec.users(user_id) ON DELETE CASCADE,
    role_name     NAME NOT NULL,
    login_time    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_activity TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_ip     INET,
    application   VARCHAR(255)
);

COMMENT ON TABLE sec.active_sessions IS 'Активные сессии пользователей (для завершения при блокировке)';

-- --------------------------------------------------------
-- Журнал регистрации пользователей (sec.register_user)
-- --------------------------------------------------------
CREATE TABLE sec.registration_log (
    reg_id      BIGSERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES core.employees(employee_id),
    login_name  VARCHAR(100) NOT NULL,
    action      VARCHAR(50) NOT NULL,  -- create, update, unlock, lock, dismiss
    performed_by INTEGER REFERENCES core.employees(employee_id) ON DELETE SET NULL,
    performed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason      TEXT
);

COMMENT ON TABLE sec.registration_log IS 'Журнал действий регистрации пользователей (sec.register_user)';
