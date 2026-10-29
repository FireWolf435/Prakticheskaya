-- ============================================================================
-- Пункт 4.1 ТЗ — запросы к БД (не менее 12).
--
-- Демонстрируются: оконные функции (rank, lag/lead), CTE и рекурсивные CTE с
-- защитой от циклов, LATERAL, FILTER, GROUPING SETS, полнотекстовый поиск,
-- NOT EXISTS, generate_series, работа с jsonb.
--
-- Запуск:
--   docker compose exec -T postgres psql -U dba_admin -d servicedesk -f /db/queries/41_queries.sql
-- Из хоста (репозиторий смонтирован не целиком) удобнее:
--   docker compose cp db/queries/41_queries.sql postgres:/tmp/41_queries.sql
--   docker compose exec -T postgres psql -U dba_admin -d servicedesk -f /tmp/41_queries.sql
-- ============================================================================

-- ЗАПРОС 16. Динамика заявок по неделям.
SELECT * FROM core.report_weekly_dynamics(now() - interval '12 weeks', now());

-- ЗАПРОС 17. Распределение заявок по часам суток (московское время).
SELECT * FROM core.report_ticket_hours(now() - interval '30 days', now());
-- (содержимое запросов 1–15 ниже — без изменений, см. исходный файл)

-- ----------------------------------------------------------------------------
-- ЗАПРОС 1. Оконная функция rank(): топ-5 исполнителей по закрытым заявкам
--           в каждом месяце 2026 года.
-- ----------------------------------------------------------------------------
WITH monthly AS (
    SELECT date_trunc('month', t.closed_at) AS month,
           e.last_name || ' ' || e.first_name AS engineer,
           e.employee_id,
           count(*) AS closed_cnt
    FROM core.tickets t
    JOIN core.employees e ON e.employee_id = t.executor_id
    WHERE t.status = 'Закрыта'
      AND t.closed_at >= timestamptz '2026-01-01 00:00+03'
      AND t.closed_at <  timestamptz '2027-01-01 00:00+03'
    GROUP BY 1,2,3
),
ranked AS (
    SELECT month, engineer, employee_id, closed_cnt,
           rank() OVER (PARTITION BY month ORDER BY closed_cnt DESC) AS rnk
    FROM monthly
)
SELECT to_char(month,'YYYY-MM') AS month,
       engineer, closed_cnt, rnk
FROM ranked
WHERE rnk <= 5
ORDER BY month, rnk;

-- ----------------------------------------------------------------------------
-- ЗАПРОС 2. Оконные функции lag()/lead(): динамика подачи заявок по месяцам
--           и изменение относительно предыдущего месяца.
-- ----------------------------------------------------------------------------
WITH monthly AS (
    SELECT date_trunc('month', t.created_at) AS month,
           count(*) AS cnt
    FROM core.tickets t
    WHERE t.created_at >= timestamptz '2026-01-01 00:00+03'
      AND t.created_at <  timestamptz '2027-01-01 00:00+03'
    GROUP BY 1
)
SELECT to_char(month,'YYYY-MM') AS month,
       cnt,
       lag(cnt)  OVER (ORDER BY month) AS prev_month,
       cnt - lag(cnt) OVER (ORDER BY month) AS delta,
       lead(cnt) OVER (ORDER BY month) AS next_month,
       round(100.0 * cnt / sum(cnt) OVER (), 2) AS pct_of_year
FROM monthly
ORDER BY month;

-- ----------------------------------------------------------------------------
-- ЗАПРОС 3. Рекурсивный CTE: все подразделения, вложенные в корневое,
--           с защитой от циклов.
-- ----------------------------------------------------------------------------
WITH RECURSIVE dept_tree AS (
    SELECT d.department_id, d.parent_id, d.name, d.code, 1 AS depth,
           ARRAY[d.department_id] AS path
    FROM ref.departments d
    WHERE d.parent_id IS NULL
    UNION ALL
    SELECT ch.department_id, ch.parent_id, ch.name, ch.code, dt.depth + 1,
           dt.path || ch.department_id
    FROM ref.departments ch
    JOIN dept_tree dt ON dt.department_id = ch.parent_id
    WHERE NOT (ch.department_id = ANY(dt.path))   -- защита от цикла
)
SELECT department_id, parent_id, name, code, depth, path
FROM dept_tree
ORDER BY depth, department_id;

-- ----------------------------------------------------------------------------
-- ЗАПРОС 4. Рекурсивный CTE: цепочка руководителей подразделения
--           (вверх по родителям), с защитой от циклов.
-- ----------------------------------------------------------------------------
WITH RECURSIVE up_path AS (
    SELECT d.department_id, d.parent_id, d.name, d.code, d.head_employee_id,
           1 AS step, ARRAY[d.department_id] AS path
    FROM ref.departments d
    WHERE d.department_id = 1
    UNION ALL
    SELECT p.department_id, p.parent_id, p.name, p.code, p.head_employee_id,
           u.step + 1, u.path || p.department_id
    FROM ref.departments p
    JOIN up_path u ON p.department_id = u.parent_id
    WHERE NOT (p.department_id = ANY(u.path))   -- защита от цикла
)
SELECT up.step,
       up.department_id,
       up.name AS department,
       up.code,
       up.head_employee_id,
       COALESCE(e.last_name || ' ' || e.first_name, '(нет руководителя)') AS head_name
FROM up_path up
LEFT JOIN core.employees e ON e.employee_id = up.head_employee_id
ORDER BY up.step;

-- ----------------------------------------------------------------------------
-- ЗАПРОС 5. LATERAL: последнее перемещение по каждому единице оборудования.
-- ----------------------------------------------------------------------------
SELECT a.inv_no, a.name, a.serial_no,
       m.moved_at, m.movement_type, m.to_location
FROM core.assets a
LEFT JOIN LATERAL (
    SELECT x.moved_at, x.movement_type, x.to_location
    FROM core.asset_movements x
    WHERE x.asset_id = a.asset_id
    ORDER BY x.moved_at DESC
    LIMIT 1
) m ON true
ORDER BY a.inv_no
LIMIT 20;

-- ----------------------------------------------------------------------------
-- ЗАПРОС 6. LATERAL: три последние заявки по каждой категории.
-- ----------------------------------------------------------------------------
SELECT c.name AS category, t.ticket_number, t.created_at, t.status
FROM ref.categories c
CROSS JOIN LATERAL (
    SELECT x.ticket_number, x.created_at, x.status
    FROM core.tickets x
    WHERE x.category_id = c.category_id
    ORDER BY x.created_at DESC
    LIMIT 3
) t
ORDER BY c.name, t.created_at DESC;

-- ----------------------------------------------------------------------------
-- ЗАПРОС 7. FILTER: сводка по приоритетам (количество, закрытые, открытые,
--           среднее время решения).
-- ----------------------------------------------------------------------------
SELECT p.priority,
       count(t.ticket_id)                                         AS total,
       count(t.ticket_id) FILTER (WHERE t.status = 'Закрыта')      AS closed,
       count(t.ticket_id) FILTER (WHERE t.status <> 'Закрыта')     AS open,
       round(avg(EXTRACT(EPOCH FROM (t.closed_at - t.created_at))/3600.0)
             FILTER (WHERE t.status = 'Закрыта'), 2)              AS avg_hours
FROM ref.priorities_sla p
LEFT JOIN core.tickets t ON t.priority = p.priority_id
GROUP BY p.priority
ORDER BY p.priority;

-- ----------------------------------------------------------------------------
-- ЗАПРОС 8. GROUPING SETS: динамика заявок по месяцам и по категориям.
-- ----------------------------------------------------------------------------
SELECT EXTRACT(year  FROM t.created_at)::int AS yr,
       EXTRACT(month FROM t.created_at)::int AS mo,
       t.category_id,
       count(*) AS cnt
FROM core.tickets t
WHERE t.created_at >= timestamptz '2026-01-01 00:00+03'
  AND t.created_at <  timestamptz '2027-01-01 00:00+03'
GROUP BY GROUPING SETS (
    (EXTRACT(year  FROM t.created_at), EXTRACT(month FROM t.created_at)),
    (t.category_id),
    ()
)
ORDER BY yr NULLS LAST, mo NULLS LAST, category_id NULLS LAST;

-- ----------------------------------------------------------------------------
-- ЗАПРОС 9. Полнотекстовый поиск по тексту заявки (русский словарь).
-- ----------------------------------------------------------------------------
SELECT t.ticket_number, t.subject,
       ts_rank(t.description_tsv,
               plainto_tsquery('russian','принтер не печатает')) AS rank,
       ts_headline('russian', t.description,
                   plainto_tsquery('russian','принтер не печатает'),
                   'StartSel=<<, StopSel=>>, MaxWords=20, MinWords=10') AS snippet
FROM core.tickets t
WHERE t.description_tsv @@ plainto_tsquery('russian','принтер не печатает')
ORDER BY rank DESC
LIMIT 10;

-- ----------------------------------------------------------------------------
-- ЗАПРОС 10. NOT EXISTS: категории без заявок.
-- ----------------------------------------------------------------------------
SELECT c.category_id, c.code, c.name
FROM ref.categories c
WHERE NOT EXISTS (SELECT 1 FROM core.tickets t WHERE t.category_id = c.category_id)
ORDER BY c.category_id;

-- ----------------------------------------------------------------------------
-- ЗАПРОС 11. generate_series: календарь заявок по дням за месяц.
-- ----------------------------------------------------------------------------
SELECT d.day::date AS day,
       count(t.ticket_id) AS created_cnt,
       count(t.ticket_id) FILTER (WHERE t.status = 'Закрыта') AS closed_cnt
FROM generate_series(timestamptz '2026-09-01 00:00+03',
                     timestamptz '2026-09-30 23:59+03', interval '1 day') AS d(day)
LEFT JOIN core.tickets t
       ON t.created_at >= d.day
      AND t.created_at <  d.day + interval '1 day'
GROUP BY d.day
ORDER BY d.day;

-- ----------------------------------------------------------------------------
-- ЗАПРОС 12. jsonb: разбор содержимого журнала аудита (кто и что менял).
-- ----------------------------------------------------------------------------
SELECT l.log_id, l.table_name, l.operation, l.changed_at, l.changed_by,
       l.new_values ->> 'status' AS new_status,
       l.old_values ->> 'status' AS old_status
FROM audit.log l
WHERE l.table_name = 'core.tickets'
  AND l.new_values ? 'status'
ORDER BY l.changed_at DESC
LIMIT 20;

-- ----------------------------------------------------------------------------
-- ЗАПРОС 13. Очередь непереданных событий (outbox) + количество попыток.
-- ----------------------------------------------------------------------------
SELECT aggregate_type, event_type, count(*) AS cnt,
       min(created_at) AS oldest, max(attempts) AS max_attempts
FROM core.outbox
WHERE processed_at IS NULL
GROUP BY aggregate_type, event_type
ORDER BY oldest;

-- ----------------------------------------------------------------------------
-- ЗАПРОС 14. NOT EXISTS: заявки, у которых нет истории статусов (сироты).
-- ----------------------------------------------------------------------------
SELECT t.ticket_number, t.status, t.created_at
FROM core.tickets t
WHERE NOT EXISTS (SELECT 1 FROM core.ticket_status_history h
                  WHERE h.ticket_id = t.ticket_id)
ORDER BY t.created_at
LIMIT 20;

-- ----------------------------------------------------------------------------
-- ЗАПРОС 15. Заявки с нарушением срока решения (SLA), отсортированные по
--            величине просрочки.
-- ----------------------------------------------------------------------------
SELECT t.ticket_number, t.priority, t.created_at, t.sla_deadline, t.closed_at,
       round(EXTRACT(EPOCH FROM (t.closed_at - t.sla_deadline)) / 3600.0, 2) AS overdue_hours
FROM core.tickets t
WHERE t.status = 'Закрыта'
  AND t.closed_at > t.sla_deadline
ORDER BY overdue_hours DESC
LIMIT 20;
