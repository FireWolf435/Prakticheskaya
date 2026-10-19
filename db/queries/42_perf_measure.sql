-- ============================================================================
-- Замеры производительности запросов (ТЗ п. 4.1, «до/после» оптимизации).
--
-- Скрипт запускается дважды на одном и том же наборе данных (1 021 580
-- заявок): сначала без новых индексов, затем после db/init/10_indexes.sql.
-- План и реальное время показывает EXPLAIN (ANALYZE, BUFFERS).
--
-- Результаты сведены в docs/perf_queries_report.txt.
-- ============================================================================

\timing on

\echo ============================================================
\echo 1. ОЧЕРЕДЬ ОПЕРАТОРА: открытые заявки по приоритету
\echo    индекс: idx_tickets_open_queue (частичный, составной)
\echo ============================================================
EXPLAIN (ANALYZE, BUFFERS)
SELECT ticket_number, priority, created_at
FROM core.tickets
WHERE status <> 'Закрыта'
ORDER BY priority, created_at
LIMIT 20;

\echo ============================================================
\echo 2. ТРИ ПОСЛЕДНИЕ ЗАЯВКИ ПО КАЖДОЙ КАТЕГОРИИ (LATERAL)
\echo    индекс: idx_tickets_category_created (составной)
\echo ============================================================
EXPLAIN (ANALYZE, BUFFERS)
SELECT c.name, t.ticket_number, t.created_at
FROM ref.categories c
CROSS JOIN LATERAL (
    SELECT x.ticket_number, x.created_at
    FROM core.tickets x
    WHERE x.category_id = c.category_id
    ORDER BY x.created_at DESC
    LIMIT 3
) t
ORDER BY c.name;

\echo ============================================================
\echo 3. КОЛИЧЕСТВО ЗАКРЫТЫХ ЗАЯВОК С НАРУШЕНИЕМ СРОКА РЕШЕНИЯ
\echo    индекс: idx_tickets_overdue (частичный)
\echo ============================================================
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) AS overdue
FROM core.tickets
WHERE status = 'Закрыта'
  AND closed_at > sla_deadline;

\echo ============================================================
\echo 4. ЗАДАЧИ НА РАЗБОР: просрочка подтверждена историей статусов
\echo    индекс: idx_history_to_status
\echo ============================================================
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) AS overdue_with_solution
FROM core.tickets t
WHERE t.status = 'Закрыта'
  AND t.closed_at > t.sla_deadline
  AND EXISTS (SELECT 1 FROM core.ticket_status_history h
              WHERE h.ticket_id = t.ticket_id AND h.to_status = 'Решена');

\echo ============================================================
\echo 5а. ПОЛНОТЕКСТОВЫЙ ПОИСК ЧЕРЕЗ LIKE (индекс неприменим)
\echo ============================================================
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM core.tickets WHERE description ILIKE '%принтер%';

\echo ============================================================
\echo 5б. ПОЛНОТЕКСТОВЫЙ ПОИСК ЧЕРЕЗ GIN (tsvector)
\echo     индекс: idx_tickets_description_tsv (GIN)
\echo ============================================================
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM core.tickets
WHERE description_tsv @@ plainto_tsquery('russian', 'принтер');

\echo ============================================================
\echo 6. ЗАГРУЗКА ОДНОГО ИСПОЛНИТЕЛЯ (как в core.auto_assign)
\echo    индекс: idx_tickets_executor_open (частичный, составной)
\echo ============================================================
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) AS open_cnt
FROM core.tickets t
WHERE t.executor_id = 241
  AND t.status IN ('Назначена', 'В работе', 'Переоткрыта', 'Ожидание пользователя');

\echo ============================================================
\echo 7. ИСТОРИЯ ОДНОЙ ЗАЯВКИ (внешний ключ ticket_id)
\echo    индекс: idx_history_ticket
\echo ============================================================
EXPLAIN (ANALYZE, BUFFERS)
SELECT h.changed_at, h.from_status, h.to_status
FROM core.ticket_status_history h
WHERE h.ticket_id = (SELECT min(ticket_id) FROM core.ticket_status_history)
ORDER BY h.changed_at;

\echo ============================================================
\echo 8. ГДЕ ИНДЕКС НЕ ПОМОГАЕТ: сводный отчёт по ВСЕМ исполнителям
\echo    (читается почти вся таблица — дешевле последовательное сканирование)
\echo ============================================================
EXPLAIN (ANALYZE, BUFFERS)
SELECT e.employee_id, count(t.ticket_id) AS open_cnt
FROM core.employees e
LEFT JOIN core.tickets t
       ON t.executor_id = e.employee_id AND t.status <> 'Закрыта'
GROUP BY e.employee_id
ORDER BY open_cnt DESC
LIMIT 10;

\echo ============================================================
\echo РАЗМЕРЫ ТАБЛИЦ И ИНДЕКСОВ
\echo ============================================================
SELECT n.nspname || '.' || c.relname AS tbl,
       pg_size_pretty(pg_relation_size(c.oid))       AS data,
       pg_size_pretty(pg_indexes_size(c.oid))        AS idx,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname IN ('core', 'ref', 'sec', 'audit')
ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 6;

SELECT indexrelid::regclass AS index_name,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_index
WHERE indrelid IN ('core.tickets'::regclass, 'core.ticket_status_history'::regclass)
ORDER BY pg_relation_size(indexrelid) DESC;
