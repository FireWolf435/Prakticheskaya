-- ============================================================================
-- Однократный пересчёт срока SLA и затраченного рабочего времени для заявок,
-- загруженных ДО появления триггера core.before_ticket_insert (ТЗ п. 4.1).
--
-- Зачем: срок SLA раньше не хранился в базе — он считался только в отчётном
-- скрипте analysis/sla.py. После появления триггера срок проставляется
-- автоматически при создании заявки, но у уже загруженных строк он пуст.
--
-- На время пересчёта пользовательские триггеры таблицы отключаются:
--   * иначе миллион обновлённых строк породил бы миллион записей аудита;
--   * сдвинулись бы updated_at и version у всех исторических заявок.
-- Триггеры включаются обратно в той же транзакции.
--
-- Запуск:
--   docker compose cp db/scripts/backfill_sla.sql postgres:/tmp/backfill_sla.sql
--   docker compose exec -T postgres psql -U dba_admin -d servicedesk -f /tmp/backfill_sla.sql
-- ============================================================================

\timing on

BEGIN;

ALTER TABLE core.tickets DISABLE TRIGGER USER;

UPDATE core.tickets
   SET sla_deadline  = core.calc_sla_deadline(created_at, priority),
       sla_spent_min = CASE
                           WHEN closed_at IS NOT NULL
                           THEN greatest(0,
                                    core.working_minutes(created_at, closed_at)
                                    - coalesce(sla_paused_total, 0))
                           ELSE sla_spent_min
                       END
 WHERE sla_deadline IS NULL;

ALTER TABLE core.tickets ENABLE TRIGGER USER;

COMMIT;

ANALYZE core.tickets;

\echo === результат пересчёта ===
SELECT count(*)                                        AS total,
       count(sla_deadline)                             AS with_deadline,
       count(closed_at)                                AS with_closed_at,
       count(*) FILTER (WHERE sla_spent_min > 0)       AS with_spent_time,
       count(*) FILTER (WHERE closed_at > sla_deadline) AS overdue_closed
FROM core.tickets;
