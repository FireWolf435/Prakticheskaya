-- ============================================================================
-- Схемы ref — справочники
-- ============================================================================

-- --------------------------------------------------------
-- Подразделения (иерархия любой глубины, есть руководитель)
-- --------------------------------------------------------
CREATE TABLE ref.departments (
    department_id   SMALLSERIAL PRIMARY KEY,
    code            VARCHAR(20) NOT NULL UNIQUE,
    name            VARCHAR(255) NOT NULL,
    parent_id       SMALLINT REFERENCES ref.departments(department_id) ON DELETE SET NULL,
    head_employee_id INTEGER,  -- заполняется после создания employees (circular FK через ALTER)
    description     TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE ref.departments IS 'Подразделения организации (иерархическая структура)';
COMMENT ON COLUMN ref.departments.parent_id IS 'Родительское подразделение (NULL — корень)';
COMMENT ON COLUMN ref.departments.head_employee_id IS 'Руководитель подразделения (ссылка на core.employees)';

-- --------------------------------------------------------
-- Физические места размещения оборудования
-- (корпуса/кабинеты, склад, серверная, кроссовая, цеха)
-- Отличаются от подразделений: местонахождение — это «где стоит техника»,
-- а не «кто отвечает». Заполняется при трансформации CMDB (п. 3.3 ТЗ).
-- --------------------------------------------------------
CREATE TABLE ref.locations (
    location_id     SMALLSERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL UNIQUE,  -- как в источнике, напр. «Корпус А, каб. 128»
    kind            VARCHAR(50),                   -- категория места: кабинет, склад, серверная, кроссовая, цех
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE ref.locations IS 'Физические места размещения оборудования (кабинеты, склады, серверные и т.п.)';
COMMENT ON COLUMN ref.locations.kind IS 'Тип места: кабинет / склад / серверная / кроссовая / цех';

-- --------------------------------------------------------
-- Категории заявок (со связью к легаси-названиям)
-- --------------------------------------------------------
CREATE TABLE ref.categories (
    category_id     SMALLSERIAL PRIMARY KEY,
    code            VARCHAR(50) NOT NULL UNIQUE,
    name            VARCHAR(255) NOT NULL,
    legacy_name     VARCHAR(255),  -- название в старой системе
    description     TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE ref.categories IS 'Категории заявок (с отражением легаси-названий)';

-- --------------------------------------------------------
-- Нормативы SLA по приоритетам (в рабочих часах)
-- --------------------------------------------------------
CREATE TABLE ref.priorities_sla (
    priority_id     SMALLSERIAL PRIMARY KEY,
    priority        ref.ticket_priority NOT NULL UNIQUE,
    sla_hours       NUMERIC(5,2) NOT NULL CHECK (sla_hours > 0),
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE ref.priorities_sla IS 'Нормативы SLA в рабочих часах по приоритетам';

-- --------------------------------------------------------
-- Производственный календарь (рабочие/выходные дни, сокращённые дни)
-- --------------------------------------------------------
CREATE TABLE ref.production_calendar (
    cal_date        DATE PRIMARY KEY,
    is_workday      BOOLEAN NOT NULL DEFAULT TRUE,
    is_short_day    BOOLEAN NOT NULL DEFAULT FALSE,
    work_start      TIME NOT NULL DEFAULT '09:00',
    work_end        TIME NOT NULL DEFAULT '18:00',
    break_start     TIME NOT NULL DEFAULT '13:00',
    break_end       TIME NOT NULL DEFAULT '14:00',
    note            VARCHAR(255)
);

COMMENT ON TABLE ref.production_calendar IS 'Производственный календарь (рабочие часы, обед, сокращённые дни)';

-- --------------------------------------------------------
-- Навыки инженеров по категориям
-- --------------------------------------------------------
CREATE TABLE ref.engineer_skills (
    skill_id        SERIAL PRIMARY KEY,
    employee_id     INTEGER NOT NULL,
    category_id     SMALLINT NOT NULL REFERENCES ref.categories(category_id) ON DELETE CASCADE,
    level           SMALLINT NOT NULL CHECK (level BETWEEN 1 AND 5),
    confirmed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (employee_id, category_id)
);

COMMENT ON TABLE ref.engineer_skills IS 'Навыки инженеров по категориям заявок (уровень 1..5)';

-- --------------------------------------------------------
-- График дежурств (смены инженеров)
-- --------------------------------------------------------
CREATE TABLE ref.duty_schedule (
    schedule_id     SERIAL PRIMARY KEY,
    engineer_id     INTEGER NOT NULL,
    shift_start     TIMESTAMPTZ NOT NULL,
    shift_end       TIMESTAMPTZ NOT NULL,
    is_primary      BOOLEAN NOT NULL DEFAULT TRUE,
    note            VARCHAR(255),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_shift_order CHECK (shift_end > shift_start)
);

COMMENT ON TABLE ref.duty_schedule IS 'График дежурств инженеров (смены не должны пересекаться)';

-- ============================================================================
-- Ограничение: смены одного инженера не могут пересекаться (EXCLUDE с gist)
-- Используем tstzrange с границами [) — начало включено, конец исключено.
-- Это означает, что если одна смена заканчивается в 18:00, а следующая начинается
-- в 18:00 — они НЕ считаются пересекающимися (инженер успевает смениться).
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE ref.duty_schedule
ADD CONSTRAINT duty_schedule_no_overlap
EXCLUDE USING gist (
    engineer_id WITH =,
    tstzrange(shift_start, shift_end, '[)') WITH &&
);

COMMENT ON CONSTRAINT duty_schedule_no_overlap ON ref.duty_schedule IS
'Запрет накладывающихся смен одного инженера. Диапазон [) означает, что конец одной смены может совпадать с началом следующей.';

-- --------------------------------------------------------
-- Допустимые переходы статусов заявки (жизненный цикл, ТЗ п. 1.3)
-- Запрет недопустимых переходов обеспечивает СУБД (триггер + эта таблица).
-- Список статический (не выводится из исходных данных), поэтому заполняется
-- прямо здесь, а не загрузчиком справочников.
-- --------------------------------------------------------
CREATE TABLE ref.ticket_transitions (
    from_status  ref.ticket_status NOT NULL,
    to_status    ref.ticket_status NOT NULL,
    description  VARCHAR(255),
    PRIMARY KEY (from_status, to_status),
    CONSTRAINT chk_transition_differs CHECK (from_status <> to_status)
);

COMMENT ON TABLE ref.ticket_transitions IS
'Допустимые переходы статусов заявки (жизненный цикл из ТЗ п. 1.3)';

INSERT INTO ref.ticket_transitions (from_status, to_status, description) VALUES
    ('Новая',                 'Назначена',           'Назначение исполнителя'),
    ('Новая',                 'Отклонена',           'Отклонение без выполнения'),
    ('Назначена',             'В работе',            'Исполнитель приступил к работе'),
    ('Назначена',             'Отклонена',           'Отклонение после назначения'),
    ('В работе',              'Ожидание пользователя', 'Ожидание ответа пользователя (таймер SLA на паузе)'),
    ('В работе',              'Решена',              'Работа завершена'),
    ('Ожидание пользователя', 'В работе',            'Возобновление работы'),
    ('Решена',                'Закрыта',             'Подтверждено, заявка закрыта'),
    ('Решена',                'Переоткрыта',         'Переоткрытие в течение 72 ч после решения'),
    ('Переоткрыта',           'В работе',            'Возобновление работы по переоткрытой заявке'),
    ('Переоткрыта',           'Решена',              'Повторное решение')
ON CONFLICT DO NOTHING;
