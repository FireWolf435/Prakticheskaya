-- ============================================================================
-- Схема core — предметная область
-- ============================================================================

-- --------------------------------------------------------
-- Сотрудники (кадровые данные, привязка к подразделению)
-- --------------------------------------------------------
CREATE TABLE core.employees (
    employee_id     SERIAL PRIMARY KEY,
    tab_number      VARCHAR(20) NOT NULL UNIQUE,  -- табельный номер (сопоставление с кадрами)
    snils           ref.snils,                     -- СНИЛС (уникален среди действующих, см. индекс ниже)
    email           ref.email UNIQUE,
    phone           ref.phone,
    birth_date      ref.birth_date,
    first_name      VARCHAR(100) NOT NULL,
    last_name       VARCHAR(100) NOT NULL,
    middle_name     VARCHAR(100),
    department_id   SMALLINT REFERENCES ref.departments(department_id) ON DELETE SET NULL,
    position        VARCHAR(255),
    hire_date       DATE NOT NULL,
    dismissal_date  DATE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    ldap_dn         VARCHAR(512),                 -- DN в OpenLDAP
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    version         INTEGER NOT NULL DEFAULT 1,   -- оптимистичная блокировка
    CONSTRAINT chk_dismissal_after_hire CHECK (
        dismissal_date IS NULL OR dismissal_date >= hire_date
    ),
    CONSTRAINT chk_name_not_empty CHECK (
        trim(first_name) <> '' AND trim(last_name) <> ''
    )
);

COMMENT ON TABLE core.employees IS 'Сотрудники организации (кадровые данные)';
COMMENT ON COLUMN core.employees.tab_number IS 'Табельный номер — ключ сопоставления с кадровой системой';
COMMENT ON COLUMN core.employees.ldap_dn IS 'Идентифицирующее имя в OpenLDAP';

-- Восстанавливаем циклическую ссылку: руководитель отдела -> сотрудник
ALTER TABLE ref.departments
ADD CONSTRAINT fk_department_head
FOREIGN KEY (head_employee_id) REFERENCES core.employees(employee_id) ON DELETE SET NULL;

-- Частичный уникальный индекс (ТЗ, п. 4.1.6): один СНИЛС — не более одного
-- ДЕЙСТВУЮЩЕГО трудового периода. Уволенные сотрудники с тем же СНИЛС
-- (повторный приём на работу) не конфликтуют, т.к. dismissal_date заполнена.
CREATE UNIQUE INDEX uq_employees_snils_active
    ON core.employees (snils)
    WHERE dismissal_date IS NULL;

COMMENT ON INDEX core.uq_employees_snils_active IS
'Один СНИЛС — не более одного действующего трудового периода (partial unique)';

-- --------------------------------------------------------
-- Заявки (Ticket)
-- --------------------------------------------------------
CREATE TABLE core.tickets (
    ticket_id       BIGSERIAL PRIMARY KEY,
    ticket_number   VARCHAR(20) NOT NULL UNIQUE,  -- номер заявки (например, SD-2026-0001)
    author_id       INTEGER NOT NULL REFERENCES core.employees(employee_id) ON DELETE RESTRICT,
    category_id     SMALLINT NOT NULL REFERENCES ref.categories(category_id) ON DELETE RESTRICT,
    priority        ref.ticket_priority NOT NULL DEFAULT 3,
    status          ref.ticket_status NOT NULL DEFAULT 'Новая',
    subject         VARCHAR(500) NOT NULL,
    description     TEXT,
    description_tsv tsvector GENERATED ALWAYS AS (to_tsvector('russian', coalesce(subject, '') || ' ' || coalesce(description, ''))) STORED,
    executor_id     INTEGER REFERENCES core.employees(employee_id) ON DELETE SET NULL,  -- исполнитель
    department_id   SMALLINT REFERENCES ref.departments(department_id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    version         INTEGER NOT NULL DEFAULT 1,   -- оптимистичная блокировка
    sla_deadline    TIMESTAMPTZ,                  -- 계산ная дата истечения SLA
    sla_spent_min   INTEGER NOT NULL DEFAULT 0,   -- затрачено рабочих минут (без пауз)
    sla_paused_at   TIMESTAMPTZ,                  -- момент постановки на паузу (Ожидание пользователя)
    sla_paused_total INTEGER NOT NULL DEFAULT 0,   -- суммарное время паузы в минутах
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT chk_subject_not_empty CHECK (trim(subject) <> '')
);

COMMENT ON TABLE core.tickets IS 'Заявки службы поддержки (Service Desk)';
COMMENT ON COLUMN core.tickets.description_tsv IS 'Полнотекстовый индекс для поиска по заявкам (русский язык)';
COMMENT ON COLUMN core.tickets.sla_deadline IS 'Срок решения с учётом производственного календаря';
COMMENT ON COLUMN core.tickets.sla_paused_at IS 'Момент остановки таймера SLA (статус Ожидание пользователя)';

-- Индекс для полнотекстового поиска
CREATE INDEX idx_tickets_description_tsv ON core.tickets USING GIN (description_tsv);

-- --------------------------------------------------------
-- История изменения статусов заявки
-- --------------------------------------------------------
CREATE TABLE core.ticket_status_history (
    history_id      BIGSERIAL PRIMARY KEY,
    ticket_id       BIGINT NOT NULL REFERENCES core.tickets(ticket_id) ON DELETE CASCADE,
    from_status     ref.ticket_status,
    to_status       ref.ticket_status NOT NULL,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    changed_by      INTEGER REFERENCES core.employees(employee_id) ON DELETE SET NULL,
    comment         TEXT,
    sla_spent_at_moment INTEGER NOT NULL DEFAULT 0,
    is_sla_violated BOOLEAN NOT NULL DEFAULT FALSE
);

COMMENT ON TABLE core.ticket_status_history IS 'Полная история изменения статусов каждой заявки';
COMMENT ON COLUMN core.ticket_status_history.is_sla_violated IS 'Признак нарушения SLA на момент перехода';

-- --------------------------------------------------------
-- Комментарии к заявкам (включая импортированные из почты)
-- --------------------------------------------------------
CREATE TABLE core.ticket_comments (
    comment_id      BIGSERIAL PRIMARY KEY,
    ticket_id       BIGINT NOT NULL REFERENCES core.tickets(ticket_id) ON DELETE CASCADE,
    author_id       INTEGER REFERENCES core.employees(employee_id) ON DELETE SET NULL,
    author_email    ref.email,                    -- если комментарий из почты внешнего автора
    body            TEXT NOT NULL,
    is_internal     BOOLEAN NOT NULL DEFAULT FALSE,  -- внутренний комментарий (не уходит клиенту)
    source          VARCHAR(50) NOT NULL DEFAULT 'ui',  -- ui, email, importer
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    version         INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT chk_comment_not_empty CHECK (trim(body) <> ''),
    CONSTRAINT chk_comment_has_author CHECK (
        author_id IS NOT NULL OR author_email IS NOT NULL
    )
);

COMMENT ON TABLE core.ticket_comments IS 'Комментарии к заявкам (включая входящие из почты)';
COMMENT ON COLUMN core.ticket_comments.source IS 'Источник комментария: ui / email / importer / mailintake';

-- --------------------------------------------------------
-- Вложения к заявкам
-- --------------------------------------------------------
CREATE TABLE core.ticket_attachments (
    attachment_id   BIGSERIAL PRIMARY KEY,
    ticket_id       BIGINT NOT NULL REFERENCES core.tickets(ticket_id) ON DELETE CASCADE,
    comment_id      BIGINT REFERENCES core.ticket_comments(comment_id) ON DELETE CASCADE,
    file_name       VARCHAR(500) NOT NULL,
    file_path       VARCHAR(1000) NOT NULL,
    file_size       BIGINT NOT NULL CHECK (file_size >= 0),
    mime_type       VARCHAR(100),
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_by     INTEGER REFERENCES core.employees(employee_id) ON DELETE SET NULL
);

COMMENT ON TABLE core.ticket_attachments IS 'Вложения к заявкам и комментариям';

-- --------------------------------------------------------
-- Оборудование (CMDB)
-- --------------------------------------------------------
CREATE TABLE core.assets (
    asset_id        SERIAL PRIMARY KEY,
    inv_no          ref.inv_no NOT NULL UNIQUE,
    serial_no       VARCHAR(200) UNIQUE,
    asset_type      VARCHAR(100) NOT NULL,
    model           VARCHAR(255),
    manufacturer    VARCHAR(255),
    state           ref.asset_state NOT NULL DEFAULT 'В эксплуатации',  -- состояние из выгрузки 1С
    cost            NUMERIC(12,2) CHECK (cost >= 0),                   -- балансовая стоимость
    purchase_date   DATE,
    warranty_until  DATE,
    responsible_id  INTEGER REFERENCES core.employees(employee_id) ON DELETE SET NULL,
    location_id     SMALLINT REFERENCES ref.locations(location_id) ON DELETE SET NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE core.assets IS 'Оборудование (CMDB) — инвентарный и серийный номер, ответственный, местонахождение';
COMMENT ON COLUMN core.assets.state IS 'Состояние единицы оборудования из выгрузки 1С';
COMMENT ON COLUMN core.assets.cost IS 'Балансовая стоимость единицы оборудования';
COMMENT ON COLUMN core.assets.location_id IS 'Текущее физическое местонахождение';

-- --------------------------------------------------------
-- История перемещений оборудования
-- --------------------------------------------------------
CREATE TABLE core.asset_movements (
    movement_id     BIGSERIAL PRIMARY KEY,
    asset_id        INTEGER NOT NULL REFERENCES core.assets(asset_id) ON DELETE CASCADE,
    from_location   SMALLINT REFERENCES ref.locations(location_id) ON DELETE SET NULL,
    to_location     SMALLINT REFERENCES ref.locations(location_id) ON DELETE SET NULL,
    moved_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    moved_by        INTEGER REFERENCES core.employees(employee_id) ON DELETE SET NULL,
    reason          VARCHAR(500),
    ticket_id       BIGINT REFERENCES core.tickets(ticket_id) ON DELETE SET NULL  -- привязка к заявке
);

COMMENT ON TABLE core.asset_movements IS 'История перемещений оборудования между подразделениями';

-- --------------------------------------------------------
-- Привязка оборудования к заявкам
-- --------------------------------------------------------
CREATE TABLE core.ticket_assets (
    ticket_id   BIGINT NOT NULL REFERENCES core.tickets(ticket_id) ON DELETE CASCADE,
    asset_id    INTEGER NOT NULL REFERENCES core.assets(asset_id) ON DELETE CASCADE,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    assigned_by INTEGER REFERENCES core.employees(employee_id) ON DELETE SET NULL,
    PRIMARY KEY (ticket_id, asset_id)
);

COMMENT ON TABLE core.ticket_assets IS 'Связь заявок с оборудованием (много ко многим)';

-- --------------------------------------------------------
-- Outbox — очередь уведомлений для notifier (LISTEN/NOTIFY)
-- --------------------------------------------------------
CREATE TABLE core.outbox (
    outbox_id       BIGSERIAL PRIMARY KEY,
    aggregate_type  VARCHAR(50) NOT NULL,   -- ticket, comment, asset
    aggregate_id    BIGINT NOT NULL,
    event_type      VARCHAR(50) NOT NULL,   -- status_changed, assigned, created, comment_added
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at    TIMESTAMPTZ,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT
);

COMMENT ON TABLE core.outbox IS 'Outbox-очередь событий для фонового отправщика уведомлений (notifier)';
CREATE INDEX idx_outbox_unprocessed ON core.outbox (created_at) WHERE processed_at IS NULL;
