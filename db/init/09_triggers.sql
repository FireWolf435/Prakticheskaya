-- ============================================================================
-- Триггеры (ТЗ п. 4.1 — не менее 6)
--   0) расчёт срока SLA при создании заявки (BEFORE INSERT);
--   1) аудит изменений в audit.log (jsonb-дифф «было/стало», кто и когда);
--   2) запрет недопустимых переходов статусов заявки (по ref.ticket_transitions);
--   3) пауза и возобновление таймера SLA при переводе в «Ожидание пользователя»;
--   4) запись события в core.outbox и pg_notify для модуля notifier;
--   5) обновление updated_at и номера версии (оптимистичная блокировка);
--   6) событийные триггеры: журналирование DDL, GRANT и REVOKE.
--   + запись истории статусов заявки в core.ticket_status_history.
--
-- Файл идемпотентен: триггеры предварительно удаляются (DROP TRIGGER IF EXISTS),
-- функции создаются через CREATE OR REPLACE.
--
-- ВАЖНО: массовая первичная загрузка (transform.py, analysis/gen_load.py)
-- намеренно не порождает записей аудита и уведомлений — иначе миллион
-- нагрузочных заявок завалил бы audit.log и outbox. Поэтому на core.tickets
-- аудируется только UPDATE/DELETE (не INSERT), а события outbox создаются
-- только при изменении заявки (смена статуса, назначение исполнителя).
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. АУДИТ ИЗМЕНЕНИЙ (jsonb-дифф «было/стало»)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit.log_changes()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = audit, core, ref, sec, pg_catalog
AS $$
DECLARE
    v_by     integer := nullif(current_setting('app.employee_id', true), '')::integer;
    v_row_id text;
    v_old    jsonb;
    v_new    jsonb;
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_row_id := to_jsonb(NEW) ->> TG_ARGV[0];
        v_old    := NULL;
        v_new    := to_jsonb(NEW);

    ELSIF TG_OP = 'UPDATE' THEN
        v_row_id := to_jsonb(NEW) ->> TG_ARGV[0];

        -- дифф: только реально изменившиеся поля.
        -- updated_at и version исключаем: они меняются при любом обновлении
        -- и в дифф по существу не входят.
        SELECT coalesce(jsonb_object_agg(o.key, o.value)
                        FILTER (WHERE o.value IS DISTINCT FROM n.value), '{}'::jsonb),
               coalesce(jsonb_object_agg(n.key, n.value)
                        FILTER (WHERE o.value IS DISTINCT FROM n.value), '{}'::jsonb)
          INTO v_old, v_new
          FROM jsonb_each(to_jsonb(OLD)) o
          JOIN jsonb_each(to_jsonb(NEW)) n ON n.key = o.key
         WHERE o.key NOT IN ('updated_at', 'version');

        IF v_new = '{}'::jsonb THEN
            RETURN NULL;                  -- изменений по существу нет
        END IF;

    ELSE  -- DELETE
        v_row_id := to_jsonb(OLD) ->> TG_ARGV[0];
        v_old    := to_jsonb(OLD);
        v_new    := NULL;
    END IF;

    -- Хеши паролей в журнал не пишем (чтобы секреты не оседали в audit.log)
    IF TG_TABLE_SCHEMA = 'sec' AND TG_TABLE_NAME = 'users' THEN
        v_old := v_old - 'password_hash';
        v_new := v_new - 'password_hash';
    END IF;

    INSERT INTO audit.log (schema_name, table_name, operation, row_id,
                           old_values, new_values, changed_by, changed_by_role,
                           session_id)
    VALUES (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
            v_old, v_new, v_by, session_user, pg_backend_pid());

    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION audit.log_changes() IS
'Универсальный триггер аудита: jsonb-дифф «было/стало», кто и когда (TG_ARGV[0] — колонка первичного ключа)';


-- Журналирование TRUNCATE (строчных триггеров у TRUNCATE не бывает)
CREATE OR REPLACE FUNCTION audit.log_truncate()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = audit, pg_catalog
AS $$
DECLARE
    v_by integer := nullif(current_setting('app.employee_id', true), '')::integer;
BEGIN
    INSERT INTO audit.log (schema_name, table_name, operation, row_id,
                           new_values, changed_by, changed_by_role, session_id)
    VALUES (TG_TABLE_SCHEMA, TG_TABLE_NAME, 'TRUNCATE', NULL,
            jsonb_build_object('note', 'таблица очищена оператором TRUNCATE'),
            v_by, session_user, pg_backend_pid());
    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION audit.log_truncate() IS
'Журналирование TRUNCATE (statement-level триггер)';


-- ----------------------------------------------------------------------------
-- 2. ЗАПРЕТ НЕДОПУСТИМЫХ ПЕРЕХОДОВ СТАТУСОВ (ТЗ п. 1.3)
--    Запрет обеспечивает СУБД, а не приложение: проверка стоит на таблице,
--    поэтому её невозможно обойти прямым UPDATE.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.enforce_transition()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT EXISTS (SELECT 1
                       FROM ref.ticket_transitions
                       WHERE from_status = OLD.status
                         AND to_status   = NEW.status) THEN
            RAISE EXCEPTION 'Недопустимый переход статуса заявки: % -> %',
                    OLD.status, NEW.status
                USING ERRCODE = 'check_violation',
                      HINT = 'Список допустимых переходов — в таблице ref.ticket_transitions';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION core.enforce_transition() IS
'Запрет недопустимых переходов статусов заявки (таблица ref.ticket_transitions, ТЗ п. 1.3)';


-- ----------------------------------------------------------------------------
-- 3. ПАУЗА И ВОЗОБНОВЛЕНИЕ ТАЙМЕРА SLA (+ фиксация закрытия)
--    «Ожидание пользователя» останавливает таймер; возврат из этого статуса
--    добавляет время паузы к накопленному sla_paused_total.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.before_ticket_status_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
        RETURN NEW;
    END IF;

    -- Пауза / возобновление таймера SLA
    IF NEW.status = 'Ожидание пользователя' THEN
        NEW.sla_paused_at := now();
    ELSIF OLD.status = 'Ожидание пользователя' AND NEW.sla_paused_at IS NOT NULL THEN
        NEW.sla_paused_total := OLD.sla_paused_total
                              + core.working_minutes(OLD.sla_paused_at, now());
        NEW.sla_paused_at := NULL;
    END IF;

    -- Факт закрытия и затраченное время (кэшируем, чтобы не считать каждый раз)
    IF NEW.status = 'Закрыта' AND NEW.closed_at IS NULL THEN
        NEW.closed_at := now();
    END IF;

    NEW.sla_spent_min := greatest(0,
        core.working_minutes(NEW.created_at, coalesce(NEW.closed_at, now()))
        - NEW.sla_paused_total
        - CASE WHEN NEW.sla_paused_at IS NOT NULL
               THEN core.working_minutes(NEW.sla_paused_at, now())
               ELSE 0 END);

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION core.before_ticket_status_change() IS
'Пауза/возобновление таймера SLA, фиксация закрытия и затраченного времени';


-- ----------------------------------------------------------------------------
-- Запись истории статусов заявки (одна запись на каждый реальный переход)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.write_status_history()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_by      integer := nullif(current_setting('app.employee_id', true), '')::integer;
    v_comment text    := nullif(current_setting('app.change_comment', true), '');
BEGIN
    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
        RETURN NULL;
    END IF;

    INSERT INTO core.ticket_status_history
        (ticket_id, from_status, to_status, changed_by, comment,
         sla_spent_at_moment, is_sla_violated)
    VALUES (NEW.ticket_id, OLD.status, NEW.status, v_by, v_comment,
            NEW.sla_spent_min,
            NEW.sla_deadline IS NOT NULL AND now() > NEW.sla_deadline);

    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION core.write_status_history() IS
'Запись перехода статуса в core.ticket_status_history';


-- ----------------------------------------------------------------------------
-- 4. OUTBOX + pg_notify для модуля notifier
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.emit_ticket_event()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_event   varchar(50);
    v_payload jsonb;
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        v_event := CASE
                     WHEN NEW.status = 'Закрыта'   THEN 'ticket_closed'
                     WHEN NEW.status = 'Назначена' THEN 'ticket_assigned'
                     ELSE 'ticket_status_changed'
                   END;
    ELSIF NEW.executor_id IS DISTINCT FROM OLD.executor_id THEN
        v_event := 'ticket_assigned';
    ELSE
        RETURN NULL;                   -- событий для notifier нет
    END IF;

    v_payload := jsonb_build_object(
        'ticket_id',     NEW.ticket_id,
        'ticket_number', NEW.ticket_number,
        'old_status',    OLD.status,
        'new_status',    NEW.status,
        'executor_id',   NEW.executor_id,
        'priority',      NEW.priority
    );

    INSERT INTO core.outbox (aggregate_type, aggregate_id, event_type, payload)
    VALUES ('ticket', NEW.ticket_id, v_event, v_payload);

    PERFORM pg_notify('ticket_events', v_payload::text);

    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION core.emit_ticket_event() IS
'Запись события изменения заявки в core.outbox и отправка pg_notify для notifier';


-- ----------------------------------------------------------------------------
-- 5. updated_at И НОМЕР ВЕРСИИ (оптимистичная блокировка)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.touch_row()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- Метку времени и версию поднимаем только если строка изменилась по существу.
    -- Иначе UPDATE, который сам правит updated_at (например, восстановление
    -- меток в importer/transform.py), работал бы вхолостую.
    IF (to_jsonb(NEW) - 'updated_at' - 'version')
       IS DISTINCT FROM (to_jsonb(OLD) - 'updated_at' - 'version') THEN
        NEW.updated_at := now();
        IF to_jsonb(NEW) ? 'version' THEN
            NEW.version := OLD.version + 1;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION core.touch_row() IS
'Обновление updated_at и увеличение version при любом изменении строки';


-- ----------------------------------------------------------------------------
-- 6. СОБЫТИЙНЫЕ ТРИГГЕРЫ: ЖУРНАЛИРОВАНИЕ DDL, GRANT, REVOKE
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit.log_ddl()
RETURNS event_trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = audit, pg_catalog
AS $$
DECLARE
    r record;
BEGIN
    IF TG_EVENT = 'sql_drop' THEN
        FOR r IN SELECT * FROM pg_event_trigger_dropped_objects() LOOP
            INSERT INTO audit.ddl_log (event, object_type, object_name, schema_name,
                                       executed_by, sql_command, tags)
            VALUES ('DROP', coalesce(r.object_type, 'unknown'),
                    coalesce(r.object_identity, ''), coalesce(r.schema_name, ''),
                    session_user, current_query(), ARRAY[TG_TAG]);
        END LOOP;
    ELSE
        FOR r IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
            INSERT INTO audit.ddl_log (event, object_type, object_name, schema_name,
                                       executed_by, sql_command, tags)
            VALUES (TG_TAG, coalesce(r.command_tag, TG_TAG),
                    coalesce(r.object_identity, ''), coalesce(r.schema_name, ''),
                    session_user, current_query(), ARRAY[TG_TAG]);
        END LOOP;
    END IF;
END;
$$;

COMMENT ON FUNCTION audit.log_ddl() IS
'Событийный триггер: журналирование DDL, GRANT и REVOKE в audit.ddl_log';


-- ============================================================================
-- СОЗДАНИЕ ТРИГГЕРОВ
-- ============================================================================

-- --- 0. Расчёт срока SLA при создании заявки ---
DROP TRIGGER IF EXISTS trg_ticket_insert ON core.tickets;
CREATE TRIGGER trg_ticket_insert
    BEFORE INSERT ON core.tickets
    FOR EACH ROW EXECUTE FUNCTION core.before_ticket_insert();

-- --- 1. Аудит изменений ---
DROP TRIGGER IF EXISTS trg_audit_tickets ON core.tickets;
CREATE TRIGGER trg_audit_tickets
    AFTER UPDATE OR DELETE ON core.tickets
    FOR EACH ROW EXECUTE FUNCTION audit.log_changes('ticket_id');

DROP TRIGGER IF EXISTS trg_audit_tickets_truncate ON core.tickets;
CREATE TRIGGER trg_audit_tickets_truncate
    AFTER TRUNCATE ON core.tickets
    FOR EACH STATEMENT EXECUTE FUNCTION audit.log_truncate();

DROP TRIGGER IF EXISTS trg_audit_employees ON core.employees;
CREATE TRIGGER trg_audit_employees
    AFTER UPDATE OR DELETE ON core.employees
    FOR EACH ROW EXECUTE FUNCTION audit.log_changes('employee_id');

DROP TRIGGER IF EXISTS trg_audit_assets ON core.assets;
CREATE TRIGGER trg_audit_assets
    AFTER UPDATE OR DELETE ON core.assets
    FOR EACH ROW EXECUTE FUNCTION audit.log_changes('asset_id');

DROP TRIGGER IF EXISTS trg_audit_departments ON ref.departments;
CREATE TRIGGER trg_audit_departments
    AFTER UPDATE OR DELETE ON ref.departments
    FOR EACH ROW EXECUTE FUNCTION audit.log_changes('department_id');

DROP TRIGGER IF EXISTS trg_audit_users ON sec.users;
CREATE TRIGGER trg_audit_users
    AFTER INSERT OR UPDATE OR DELETE ON sec.users
    FOR EACH ROW EXECUTE FUNCTION audit.log_changes('user_id');

-- --- 2. Запрет недопустимых переходов статусов ---
DROP TRIGGER IF EXISTS trg_ticket_enforce_transition ON core.tickets;
CREATE TRIGGER trg_ticket_enforce_transition
    BEFORE UPDATE OF status ON core.tickets
    FOR EACH ROW EXECUTE FUNCTION core.enforce_transition();

-- --- 3. Пауза/возобновление таймера SLA ---
DROP TRIGGER IF EXISTS trg_ticket_sla ON core.tickets;
CREATE TRIGGER trg_ticket_sla
    BEFORE UPDATE OF status ON core.tickets
    FOR EACH ROW EXECUTE FUNCTION core.before_ticket_status_change();

-- --- Запись истории статусов ---
DROP TRIGGER IF EXISTS trg_ticket_history ON core.tickets;
CREATE TRIGGER trg_ticket_history
    AFTER UPDATE OF status ON core.tickets
    FOR EACH ROW EXECUTE FUNCTION core.write_status_history();

-- --- 4. Outbox + pg_notify ---
DROP TRIGGER IF EXISTS trg_ticket_notify ON core.tickets;
CREATE TRIGGER trg_ticket_notify
    AFTER UPDATE ON core.tickets
    FOR EACH ROW EXECUTE FUNCTION core.emit_ticket_event();

-- --- 5. updated_at и version ---
DROP TRIGGER IF EXISTS trg_ticket_touch ON core.tickets;
CREATE TRIGGER trg_ticket_touch
    BEFORE UPDATE ON core.tickets
    FOR EACH ROW EXECUTE FUNCTION core.touch_row();

DROP TRIGGER IF EXISTS trg_employees_touch ON core.employees;
CREATE TRIGGER trg_employees_touch
    BEFORE UPDATE ON core.employees
    FOR EACH ROW EXECUTE FUNCTION core.touch_row();

DROP TRIGGER IF EXISTS trg_assets_touch ON core.assets;
CREATE TRIGGER trg_assets_touch
    BEFORE UPDATE ON core.assets
    FOR EACH ROW EXECUTE FUNCTION core.touch_row();

DROP TRIGGER IF EXISTS trg_users_touch ON sec.users;
CREATE TRIGGER trg_users_touch
    BEFORE UPDATE ON sec.users
    FOR EACH ROW EXECUTE FUNCTION core.touch_row();

-- --- 6. Событийные триггеры DDL/GRANT/REVOKE ---
DROP EVENT TRIGGER IF EXISTS trg_ddl_audit;
CREATE EVENT TRIGGER trg_ddl_audit
    ON ddl_command_end
    EXECUTE FUNCTION audit.log_ddl();

DROP EVENT TRIGGER IF EXISTS trg_drop_audit;
CREATE EVENT TRIGGER trg_drop_audit
    ON sql_drop
    EXECUTE FUNCTION audit.log_ddl();
