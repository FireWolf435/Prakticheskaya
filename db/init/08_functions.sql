-- ============================================================================
-- Функции и процедуры (ТЗ п. 4.1 — не менее 7)
--   core.working_minutes            — рабочих минут между двумя моментами
--   core.calc_sla_deadline          — срок решения по календарю и приоритету
--   core.calc_sla_spent             — затраченное рабочее время без учёта паузы
--   core.before_ticket_insert       — расчёт срока SLA при создании заявки
--   core.change_ticket_status       — переход по таблице допустимых переходов
--   core.auto_assign                — назначение (конкурентно-безопасное)
--   core.reopen_ticket              — переоткрытие с проверкой окна 72 ч
--   core.move_asset                 — перемещение оборудования
--   sec.register_user               — регистрация пользователя и ролей
--   core.report_tickets_by_category — отчётная табличная функция
--   core.report_engineer_load       — отчётная табличная функция
--
-- Все функции создаются с CREATE OR REPLACE и при повторном запуске
-- db/init просто переопределяются (файл идемпотентен).
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Рабочие минуты между двумя моментами по производственному календарю.
-- Учитываются только рабочие дни, рабочие часы (9:00-18:00, в сокращённый
-- день — до 17:00) и обеденный перерыв. Момент «из» включён, «в» — нет.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.working_minutes(p_from timestamptz, p_to timestamptz)
RETURNS integer
LANGUAGE sql
STABLE
AS $$
    WITH b AS (
        SELECT (p_from AT TIME ZONE 'Europe/Moscow') AS f,
               (p_to   AT TIME ZONE 'Europe/Moscow') AS t
    ), d AS (
        SELECT COALESCE(c.is_workday, EXTRACT(ISODOW FROM days.cal_date) < 6) AS is_workday,
               days.cal_date + COALESCE(c.work_start, time '09:00') AS ws,
               days.cal_date + CASE WHEN COALESCE(c.is_short_day, false)
                                    THEN time '17:00' ELSE COALESCE(c.work_end, time '18:00') END AS we,
               days.cal_date + COALESCE(c.break_start, time '13:00') AS bs,
               days.cal_date + COALESCE(c.break_end, time '14:00') AS be,
               b.f, b.t
        FROM b
        CROSS JOIN LATERAL generate_series(b.f::date, b.t::date, interval '1 day') AS days(cal_date)
        LEFT JOIN ref.production_calendar c ON c.cal_date = days.cal_date
    )
    SELECT coalesce(round(sum(
               EXTRACT(EPOCH FROM (least(we, t) - greatest(ws, f)))
               - greatest(0, EXTRACT(EPOCH FROM (least(be, t) - greatest(bs, f))))
           ) / 60.0), 0)::integer
    FROM d
    WHERE is_workday AND EXTRACT(ISODOW FROM ws::date) < 6 AND t > ws AND f < we;
$$;

COMMENT ON FUNCTION core.working_minutes(timestamptz, timestamptz) IS
'Количество рабочих минут между двумя моментами (по производственному календарю, без обеда и нерабочих дней)';


-- ----------------------------------------------------------------------------
-- Срок решения заявки: прибавить норматив SLA (в рабочих часах) к моменту
-- старта с учётом календаря, обеда и сокращённых дней (ТЗ п. 1.4).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.calc_sla_deadline(p_start timestamptz, p_priority integer)
RETURNS timestamptz
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_need    integer;
    v_left    integer;
    v_day     date;
    v_cur     timestamp;
    v_is_work boolean;
    v_ws      time;
    v_we      time;
    v_bs      time;
    v_be      time;
    v_short   boolean;
    v_from    timestamp;
    v_to      timestamp;
    v_avail   numeric;
    v_break   numeric;
    v_before  numeric;
BEGIN
    SELECT round(sla_hours * 60) INTO v_need
    FROM ref.priorities_sla WHERE priority = p_priority;

    IF v_need IS NULL THEN
        RAISE EXCEPTION 'Нет норматива SLA для приоритета %', p_priority;
    END IF;

    v_left := v_need;
    v_cur  := p_start AT TIME ZONE 'Europe/Moscow';
    v_day  := v_cur::date;

    LOOP
        SELECT is_workday, work_start, work_end, break_start, break_end, is_short_day
          INTO v_is_work, v_ws, v_we, v_bs, v_be, v_short
          FROM ref.production_calendar WHERE cal_date = v_day;

        IF NOT FOUND THEN
            -- За пределами календаря действуем по общим правилам (пн-пт, 9-18),
            -- чтобы функция всегда завершалась и не зацикливалась.
            v_is_work := EXTRACT(ISODOW FROM v_day) < 6;
            v_ws := time '09:00'; v_we := time '18:00';
            v_bs := time '13:00'; v_be := time '14:00';
            v_short := false;
        END IF;

        -- SLA runs only on Monday-Friday; a calendar row cannot turn a
        -- weekend into a working day by mistake.
        v_is_work := v_is_work AND EXTRACT(ISODOW FROM v_day) < 6;

        IF v_is_work THEN
            v_from := greatest(v_cur, v_day + v_ws);
            v_to   := v_day + CASE WHEN v_short THEN time '17:00' ELSE v_we END;

            IF v_from < v_to THEN
                v_avail := EXTRACT(EPOCH FROM (v_to - v_from)) / 60.0;

                -- обед вычитаем только если он попал в рассматриваемый интервал
                v_break := EXTRACT(EPOCH FROM (
                    least(v_to, v_day + v_be) - greatest(v_from, v_day + v_bs)
                )) / 60.0;
                IF v_break > 0 THEN
                    v_avail := v_avail - v_break;
                END IF;

                IF v_left <= v_avail THEN
                    -- Остаток помещаем внутрь рабочего дня, перескакивая обед.
                    -- Без этого норматив «съедал» бы перерыв и срок выходил раньше.
                    IF v_from < v_day + v_bs THEN
                        v_before := EXTRACT(EPOCH FROM (v_day + v_bs - v_from)) / 60.0;
                        IF v_left <= v_before THEN
                            RETURN (v_from + make_interval(mins => v_left))
                                   AT TIME ZONE 'Europe/Moscow';
                        END IF;
                        RETURN (v_day + v_be
                                + make_interval(mins => v_left - round(v_before)::integer))
                               AT TIME ZONE 'Europe/Moscow';
                    END IF;

                    -- старт пришёлся на обеденный перерыв или после него
                    IF v_from < v_day + v_be THEN
                        v_from := v_day + v_be;
                    END IF;

                    RETURN (v_from + make_interval(mins => v_left))
                           AT TIME ZONE 'Europe/Moscow';
                END IF;

                v_left := v_left - round(v_avail)::integer;
            END IF;
        END IF;

        v_day := v_day + 1;
        v_cur := v_day;
    END LOOP;
END;
$$;

COMMENT ON FUNCTION core.calc_sla_deadline(timestamptz, integer) IS
'Срок решения заявки: норматив SLA приоритета в рабочих часах от момента старта (с календарём, обедом и сокращёнными днями)';


-- ----------------------------------------------------------------------------
-- Затраченное рабочее время по заявке без учёта пауз SLA.
-- Текущая пауза (sla_paused_at) тоже вычитается.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.calc_sla_spent(p_ticket_id bigint)
RETURNS integer
LANGUAGE sql
STABLE
AS $$
    SELECT greatest(0,
        core.working_minutes(t.created_at, coalesce(t.closed_at, now()))
        - t.sla_paused_total
        - CASE WHEN t.sla_paused_at IS NOT NULL
               THEN core.working_minutes(t.sla_paused_at, now())
               ELSE 0 END)
    FROM core.tickets t
    WHERE t.ticket_id = p_ticket_id;
$$;

COMMENT ON FUNCTION core.calc_sla_spent(bigint) IS
'Затраченное рабочее время по заявке в минутах, без учёта пауз (ТЗ п. 1.4)';


-- ----------------------------------------------------------------------------
-- Подготовка заявки при вставке (BEFORE INSERT).
-- Срок решения по SLA считает СУБД, а не приложение: так он гарантированно
-- есть у любой заявки, независимо от того, кто её создал (веб-форма, почтовый
-- робот, импорт исторических данных).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.before_ticket_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.created_at IS NULL THEN
        NEW.created_at := now();
    END IF;

    -- срок решения по нормативу приоритета (если не передан явно)
    IF NEW.sla_deadline IS NULL THEN
        NEW.sla_deadline := core.calc_sla_deadline(NEW.created_at, NEW.priority);
    END IF;

    -- заявку могут вставить сразу закрытой (импорт исторических данных) —
    -- тогда сразу посчитаем затраченное рабочее время
    IF NEW.closed_at IS NOT NULL THEN
        NEW.sla_spent_min := greatest(0,
            core.working_minutes(NEW.created_at, NEW.closed_at)
            - coalesce(NEW.sla_paused_total, 0));
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION core.before_ticket_insert() IS
'Расчёт срока SLA (и затраченного времени для уже закрытых заявок) при создании заявки';


-- ----------------------------------------------------------------------------
-- Смена статуса заявки. Допустимость перехода проверяет СУБД (триггер по
-- ref.ticket_transitions), поэтому здесь только фиксируем комментарий и
-- выполняем UPDATE — так запрет работает и для прямых UPDATE из приложений.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.change_ticket_status(
    p_ticket_id bigint,
    p_new_status ref.ticket_status,
    p_comment text DEFAULT NULL
)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_old ref.ticket_status;
BEGIN
    SELECT status INTO v_old FROM core.tickets
    WHERE ticket_id = p_ticket_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Заявка % не найдена', p_ticket_id;
    END IF;

    IF v_old = p_new_status THEN
        RETURN;                        -- переход в тот же статус не считается сменой
    END IF;

    -- комментарий для записи в историю; действует до конца транзакции
    PERFORM set_config('app.change_comment', coalesce(p_comment, ''), true);

    UPDATE core.tickets SET status = p_new_status WHERE ticket_id = p_ticket_id;
END;
$$;

COMMENT ON FUNCTION core.change_ticket_status(bigint, ref.ticket_status, text) IS
'Смена статуса заявки с проверкой допустимости перехода (таблица ref.ticket_transitions)';


-- ----------------------------------------------------------------------------
-- Автоназначение исполнителя (ТЗ п. 1.5): инженер с навыком по категории
-- заявки, наименьшей текущей загрузкой и предпочтительно на дежурстве.
-- Конкурентная безопасность: заявка блокируется FOR UPDATE SKIP LOCKED,
-- поэтому два процесса не назначат одну заявку (и не «встанут» друг за другом).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.auto_assign(p_ticket_id bigint)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_category smallint;
    v_status   ref.ticket_status;
    v_engineer integer;
BEGIN
    PERFORM 1 FROM core.tickets
    WHERE ticket_id = p_ticket_id
    FOR UPDATE SKIP LOCKED;

    IF NOT FOUND THEN
        RETURN NULL;                   -- заявку уже забрал другой процесс
    END IF;

    SELECT category_id, status INTO v_category, v_status
    FROM core.tickets WHERE ticket_id = p_ticket_id;

    SELECT e.employee_id INTO v_engineer
    FROM ref.engineer_skills s
    JOIN core.employees e
      ON e.employee_id = s.employee_id AND e.is_active
    LEFT JOIN LATERAL (
        SELECT count(*) AS load
        FROM core.tickets t
        WHERE t.executor_id = e.employee_id
          AND t.status IN ('Назначена', 'В работе', 'Переоткрыта',
                           'Ожидание пользователя')
    ) l ON true
    LEFT JOIN LATERAL (
        SELECT bool_or(d.shift_start <= now() AND d.shift_end >= now()) AS on_duty
        FROM ref.duty_schedule d
        WHERE d.engineer_id = e.employee_id
    ) d ON true
    WHERE s.category_id = v_category
    ORDER BY (coalesce(d.on_duty, false) = false), l.load, s.level DESC, e.employee_id
    LIMIT 1;

    IF v_engineer IS NULL THEN
        RETURN NULL;                   -- нет инженера с нужным навыком
    END IF;

    IF v_status = 'Новая' THEN
        -- назначение переводит новую заявку в статус «Назначена»
        UPDATE core.tickets SET executor_id = v_engineer, status = 'Назначена'
        WHERE ticket_id = p_ticket_id;
    ELSE
        UPDATE core.tickets SET executor_id = v_engineer
        WHERE ticket_id = p_ticket_id;
    END IF;

    RETURN v_engineer;
END;
$$;

COMMENT ON FUNCTION core.auto_assign(bigint) IS
'Автоназначение исполнителя по навыкам, загрузке и графику дежурств (FOR UPDATE SKIP LOCKED)';


-- ----------------------------------------------------------------------------
-- Переоткрытие заявки. Разрешено только из статуса «Решена» и не позднее
-- 72 часов с момента решения (ТЗ п. 1.3).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.reopen_ticket(p_ticket_id bigint)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_status   ref.ticket_status;
    v_resolved timestamptz;
BEGIN
    SELECT status INTO v_status FROM core.tickets
    WHERE ticket_id = p_ticket_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Заявка % не найдена', p_ticket_id;
    END IF;

    IF v_status <> 'Решена' THEN
        RAISE EXCEPTION 'Переоткрыть можно только заявку в статусе «Решена» (текущий: %)',
            v_status;
    END IF;

    SELECT max(changed_at) INTO v_resolved
    FROM core.ticket_status_history
    WHERE ticket_id = p_ticket_id AND to_status = 'Решена';

    IF v_resolved IS NOT NULL AND now() - v_resolved > interval '72 hours' THEN
        RAISE EXCEPTION 'Окно переоткрытия (72 ч) истекло: заявка решена %', v_resolved;
    END IF;

    PERFORM set_config('app.change_comment', 'Переоткрытие заявки', true);
    UPDATE core.tickets SET status = 'Переоткрыта' WHERE ticket_id = p_ticket_id;
END;
$$;

COMMENT ON FUNCTION core.reopen_ticket(bigint) IS
'Переоткрытие решённой заявки в течение 72 часов после решения';


-- ----------------------------------------------------------------------------
-- Перемещение оборудования: меняет текущее местонахождение и пишет историю
-- перемещений (ТЗ п. 1.7).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.move_asset(p_inv_no ref.inv_no, p_to_location smallint)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_asset integer;
    v_from  smallint;
    v_by    integer;
BEGIN
    v_by := nullif(current_setting('app.employee_id', true), '')::integer;

    SELECT asset_id, location_id INTO v_asset, v_from
    FROM core.assets WHERE inv_no = p_inv_no
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Оборудование % не найдено', p_inv_no;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM ref.locations WHERE location_id = p_to_location) THEN
        RAISE EXCEPTION 'Местонахождение % не найдено', p_to_location;
    END IF;

    UPDATE core.assets SET location_id = p_to_location WHERE asset_id = v_asset;

    INSERT INTO core.asset_movements (asset_id, from_location, to_location, moved_by, reason)
    VALUES (v_asset, v_from, p_to_location, v_by, 'Перемещение через core.move_asset');
END;
$$;

COMMENT ON FUNCTION core.move_asset(ref.inv_no, smallint) IS
'Перемещение оборудования в другое местонахождение с записью истории';


-- ----------------------------------------------------------------------------
-- Регистрация пользователя (ТЗ п. 4.4.3): создаёт учётную запись, выдаёт
-- роли, пишет историю пароля и журнал. Проверяет права вызывающего
-- (суперпользователь или член группы dba). SECURITY DEFINER — чтобы
-- приложение могло регистрировать пользователей, не имея прав напрямую
-- на таблицы схемы sec.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sec.register_user(
    p_employee_id   integer,
    p_login_name    varchar,
    p_password_hash text,
    p_roles         name[],
    p_performed_by  integer DEFAULT NULL,
    p_reason        text DEFAULT NULL
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = sec, core, ref, audit, pg_catalog
AS $$
DECLARE
    v_caller   name := session_user;
    v_allowed  boolean;
    v_user_id  integer;
BEGIN
    v_allowed := EXISTS (SELECT 1 FROM pg_roles
                         WHERE rolname = v_caller AND rolsuper)
              OR (EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dba')
                  AND pg_has_role(v_caller, 'dba', 'member'));

    IF NOT v_allowed THEN
        INSERT INTO audit.security_events (event_type, user_name, details)
        VALUES ('access_denied', v_caller,
                jsonb_build_object('function', 'sec.register_user',
                                   'reason', 'недостаточно прав'));
        RAISE EXCEPTION 'Недостаточно прав для регистрации пользователей'
            USING ERRCODE = '42501';
    END IF;

    INSERT INTO sec.users (employee_id, login_name, password_hash,
                           created_by, valid_until)
    VALUES (p_employee_id, p_login_name, p_password_hash,
            p_performed_by, now() + interval '1 year')
    RETURNING user_id INTO v_user_id;

    INSERT INTO sec.password_history (user_id, password_hash, changed_by)
    VALUES (v_user_id, p_password_hash, p_performed_by);

    IF p_roles IS NOT NULL THEN
        INSERT INTO sec.user_roles (user_id, role_name, granted_by)
        SELECT v_user_id, r, p_performed_by FROM unnest(p_roles) AS r
        ON CONFLICT DO NOTHING;
    END IF;

    INSERT INTO sec.registration_log (employee_id, login_name, action,
                                      performed_by, reason)
    VALUES (p_employee_id, p_login_name, 'create', p_performed_by, p_reason);

    RETURN v_user_id;
END;
$$;

COMMENT ON FUNCTION sec.register_user(integer, varchar, text, name[], integer, text) IS
'Регистрация пользователя: учётная запись, роли, история пароля, журнал; проверка прав вызывающего';


-- ----------------------------------------------------------------------------
-- Отчётная табличная функция: сводка по категориям за период (ТЗ п. 1.9).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.report_tickets_by_category(
    p_from timestamptz,
    p_to   timestamptz
)
RETURNS TABLE (
    category_name        varchar,
    total                bigint,
    closed               bigint,
    avg_resolution_hours numeric,
    sla_violations       bigint
)
LANGUAGE sql
STABLE
AS $$
    SELECT c.name::varchar,
           count(*)::bigint,
           count(*) FILTER (WHERE t.status = 'Закрыта')::bigint,
           round(avg(EXTRACT(EPOCH FROM (t.closed_at - t.created_at)) / 3600.0), 2),
           count(*) FILTER (WHERE t.sla_deadline IS NOT NULL
                              AND t.closed_at IS NOT NULL
                              AND t.closed_at > t.sla_deadline)::bigint
    FROM core.tickets t
    JOIN ref.categories c ON c.category_id = t.category_id
    WHERE t.created_at >= p_from AND t.created_at < p_to
    GROUP BY c.name
    ORDER BY 2 DESC;
$$;

COMMENT ON FUNCTION core.report_tickets_by_category(timestamptz, timestamptz) IS
'Отчёт: количество, закрытые, среднее время решения и нарушения SLA по категориям за период';


-- ----------------------------------------------------------------------------
-- Отчётная табличная функция: загрузка инженеров за период (ТЗ п. 1.9).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.report_engineer_load(
    p_from timestamptz,
    p_to   timestamptz
)
RETURNS TABLE (
    engineer_id   integer,
    engineer_name varchar,
    assigned      bigint,
    closed        bigint,
    avg_hours     numeric
)
LANGUAGE sql
STABLE
AS $$
    SELECT e.employee_id,
           (e.last_name || ' ' || e.first_name)::varchar,
           count(t.ticket_id)::bigint,
           count(t.ticket_id) FILTER (WHERE t.status = 'Закрыта')::bigint,
           round(avg(EXTRACT(EPOCH FROM (t.closed_at - t.created_at)) / 3600.0), 2)
    FROM core.employees e
    LEFT JOIN core.tickets t
           ON t.executor_id = e.employee_id
          AND t.created_at >= p_from AND t.created_at < p_to
    WHERE e.is_active
    GROUP BY e.employee_id, e.last_name, e.first_name
    ORDER BY 3 DESC, 2;
$$;

COMMENT ON FUNCTION core.report_engineer_load(timestamptz, timestamptz) IS
'Отчёт: назначено и закрыто заявок, среднее время решения по инженерам за период';

CREATE OR REPLACE FUNCTION core.report_weekly_dynamics(
    p_from timestamptz,
    p_to timestamptz
)
RETURNS TABLE (week_start date, total bigint, closed bigint, sla_violations bigint)
LANGUAGE sql STABLE AS $$
    SELECT date_trunc('week', t.created_at)::date,
           count(*)::bigint,
           count(*) FILTER (WHERE t.status = 'Закрыта')::bigint,
           count(*) FILTER (WHERE t.closed_at IS NOT NULL AND t.sla_deadline IS NOT NULL
                                  AND t.closed_at > t.sla_deadline)::bigint
      FROM core.tickets t
     WHERE t.created_at >= p_from AND t.created_at < p_to
     GROUP BY 1 ORDER BY 1;
$$;

CREATE OR REPLACE FUNCTION core.report_ticket_hours(
    p_from timestamptz,
    p_to timestamptz
)
RETURNS TABLE (hour_of_day integer, total bigint)
LANGUAGE sql STABLE AS $$
    SELECT extract(hour FROM t.created_at AT TIME ZONE 'Europe/Moscow')::integer,
           count(*)::bigint
      FROM core.tickets t
     WHERE t.created_at >= p_from AND t.created_at < p_to
     GROUP BY 1 ORDER BY 1;
$$;
