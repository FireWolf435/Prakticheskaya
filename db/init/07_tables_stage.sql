-- ============================================================================
-- Схема stage — staging-таблицы для загрузки исходных данных «как есть»
-- Все поля text, плюс src_line (номер строки в файле) и src_file (имя файла)
-- ============================================================================

-- Кадровая система: подразделения
CREATE TABLE stage.hr_departments (
    src_line  BIGINT NOT NULL,
    src_file  VARCHAR(255) NOT NULL,
    line_data TEXT NOT NULL  -- вся строка как есть (CSV с разделителем ;)
);

COMMENT ON TABLE stage.hr_departments IS 'Staging: hr/departments.csv (кадровая система, Windows-1251, разделитель ;)';

-- Кадровая система: сотрудники
CREATE TABLE stage.hr_employees (
    src_line  BIGINT NOT NULL,
    src_file  VARCHAR(255) NOT NULL,
    line_data TEXT NOT NULL
);

COMMENT ON TABLE stage.hr_employees IS 'Staging: hr/employees.csv (кадровая система, Windows-1251, разделитель ;)';

-- 1С: оборудование (xlsx)
CREATE TABLE stage.cmdb_assets_1c (
    src_line  BIGINT NOT NULL,
    src_file  VARCHAR(255) NOT NULL,
    line_data TEXT NOT NULL
);

COMMENT ON TABLE stage.cmdb_assets_1c IS 'Staging: cmdb/assets_1c_export.xlsx (1С:Бухгалтерия, ведомость ОС и МЦ)';

-- Журнал перемещений оборудования
CREATE TABLE stage.cmdb_asset_movements (
    src_line  BIGINT NOT NULL,
    src_file  VARCHAR(255) NOT NULL,
    line_data TEXT NOT NULL
);

COMMENT ON TABLE stage.cmdb_asset_movements IS 'Staging: cmdb/asset_movements.csv (журнал перемещений ИТ-отдела, CSV , UTF-8)';

-- Легаси: заявки
CREATE TABLE stage.legacy_tickets (
    src_line  BIGINT NOT NULL,
    src_file  VARCHAR(255) NOT NULL,
    line_data TEXT NOT NULL
);

COMMENT ON TABLE stage.legacy_tickets IS 'Staging: legacy/tickets.csv (старая система заявок, CSV ;, UTF-8 с BOM, многострочные поля)';

-- Легаси: история статусов
CREATE TABLE stage.legacy_ticket_status_history (
    src_line  BIGINT NOT NULL,
    src_file  VARCHAR(255) NOT NULL,
    line_data TEXT NOT NULL
);

COMMENT ON TABLE stage.legacy_ticket_status_history IS 'Staging: legacy/ticket_status_history.csv (журнал статусов легаси)';

-- Справочники: категории
CREATE TABLE stage.ref_categories (
    src_line  BIGINT NOT NULL,
    src_file  VARCHAR(255) NOT NULL,
    line_data TEXT NOT NULL
);

COMMENT ON TABLE stage.ref_categories IS 'Staging: ref/categories.csv (категории заявок)';

-- Справочники: приоритеты SLA
CREATE TABLE stage.ref_priorities_sla (
    src_line  BIGINT NOT NULL,
    src_file  VARCHAR(255) NOT NULL,
    line_data TEXT NOT NULL
);

COMMENT ON TABLE stage.ref_priorities_sla IS 'Staging: ref/priorities_sla.csv (нормативы SLA)';

-- Справочники: производственный календарь
CREATE TABLE stage.ref_production_calendar (
    src_line  BIGINT NOT NULL,
    src_file  VARCHAR(255) NOT NULL,
    line_data TEXT NOT NULL
);

COMMENT ON TABLE stage.ref_production_calendar IS 'Staging: ref/production_calendar.csv (производственный календарь)';

-- Справочники: навыки инженеров
CREATE TABLE stage.ref_engineer_skills (
    src_line  BIGINT NOT NULL,
    src_file  VARCHAR(255) NOT NULL,
    line_data TEXT NOT NULL
);

COMMENT ON TABLE stage.ref_engineer_skills IS 'Staging: ref/engineer_skills.csv (навыки инженеров)';

-- Справочники: график дежурств
CREATE TABLE stage.ref_duty_schedule (
    src_line  BIGINT NOT NULL,
    src_file  VARCHAR(255) NOT NULL,
    line_data TEXT NOT NULL
);

COMMENT ON TABLE stage.ref_duty_schedule IS 'Staging: ref/duty_schedule.csv (график дежурств)';

-- LDAP: каталог пользователей
CREATE TABLE stage.ldap_directory (
    src_line  BIGINT NOT NULL,
    src_file  VARCHAR(255) NOT NULL,
    line_data TEXT NOT NULL
);

COMMENT ON TABLE stage.ldap_directory IS 'Staging: ldap/directory.ldif (каталог OpenLDAP)';

-- Почта: письма из ящика support@
CREATE TABLE stage.mail_messages (
    src_line  BIGINT NOT NULL,
    src_file  VARCHAR(255) NOT NULL,
    line_data TEXT NOT NULL
);

COMMENT ON TABLE stage.mail_messages IS 'Staging: mail/*.eml (письма из ящика support@, RFC 5322)';

-- ============================================================================
-- Журнал импорта (отслеживание каждой строки: загружена / отклонена / исправлена)
-- ============================================================================
CREATE TABLE stage.import_log (
    import_id     BIGSERIAL PRIMARY KEY,
    source_file   VARCHAR(255) NOT NULL,
    source_table  VARCHAR(100) NOT NULL,  -- staging-таблица
    src_line      BIGINT NOT NULL,
    status        VARCHAR(20) NOT NULL CHECK (status IN ('loaded', 'rejected', 'corrected')),
    reason        TEXT,                   -- причина отклонения или исправления
    imported_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    imported_by   NAME NOT NULL DEFAULT CURRENT_USER
);

COMMENT ON TABLE stage.import_log IS 'Журнал импорта: каждая строка — загружена, отклонена или исправлена с причиной';
CREATE INDEX idx_import_log_file_status ON stage.import_log (source_file, status);

-- ============================================================================
-- Журнал обработки входящей почты (модуль mailintake, пункт 3.6 ТЗ)
-- Одно письмо mail/*.eml = одна строка: результат обработки и причина.
-- ============================================================================
CREATE TABLE stage.mail_intake_log (
    intake_id     BIGSERIAL PRIMARY KEY,
    src_file      VARCHAR(255) NOT NULL,   -- письмо (mail/001.eml)
    message_id    VARCHAR(512),            -- заголовок Message-ID
    from_email    VARCHAR(320),            -- отправитель
    subject       TEXT,                    -- тема письма (после декодирования)
    letter_date   TIMESTAMPTZ,             -- дата письма
    action        VARCHAR(20) NOT NULL CHECK (action IN
                  ('ticket_created', 'comment_added', 'skipped', 'rejected')),
    ticket_number VARCHAR(20),             -- заявка, к которой относится результат
    ticket_id     BIGINT,                  -- ссылка на core.tickets (если есть)
    reason        TEXT,                    -- причина (для skipped/rejected и пр.)
    processed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE stage.mail_intake_log IS
'Журнал mailintake: результат и причина обработки каждого письма из mail/*.eml';
COMMENT ON COLUMN stage.mail_intake_log.action IS
'Действие: ticket_created (создана заявка) / comment_added (добавлен комментарий) / skipped (пропущено) / rejected (отклонено)';
CREATE INDEX idx_mail_intake_action ON stage.mail_intake_log (action);
