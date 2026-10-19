# П. 4.3 ТЗ: Администрирование базы данных (12 ч)

## 1. Архитектура администрирования

Система Service Desk развернута в конфигурации **Docker Compose** с тремя контейнерами:
- `sd-postgres` — PostgreSQL 16 (ядро системы)
- `sd-ldap` — OpenLDAP (централизованный каталог)
- `sd-mail` — Docker Mail Server (почта)

Все данные БД хранятся в именованном объеме `pgdata`, который сохраняется при пересоздании контейнеров.

## 2. Резервное копирование

### 2.1 pg_dump (полное резервное копирование)

**Команда:**
```bash
# Запуск с хоста (репозиторий смонтирован не целиком, удобнее через docker)
docker compose exec -T postgres pg_dump -U dba_admin -d servicedesk > /tmp/sd_backup_$(date +%Y%m%d).sql
```

**или из хоста:**
```bash
PGPASSWORD=118100 pg_dump -U dba_admin -h localhost -p 5432 -d servicedesk > backup_$(date +%Y%m%d).sql
```

**Особенности:**
- `pg_dump`_consistent snapshot_ — делает снимок данных на момент старта
- Резервная копия **не блокирует** работу системы (read-consistent)
- Размер копии зависит от объема данных (~500 МБ для core.tickets на текущий момент)

### 2. pg_dump + compress (сжатие)

```bash
docker compose exec -T postgres pg_dump -U dba_admin -d servicedesk | gzip > /tmp/sd_backup_$(date +%Y%m%d).sql.gz
```
Сжатие уменьшает размер в 4–6 раз (всего несколько МБ против сотен МБ).

### 2. pg_basebackup (копия физического уровня)

Используется для точки восстановления (point-in-time recovery):
```bash
docker compose exec -T postgres pg_basebackup -U dba_admin -d servicedesk -Fp -Xs -P -D /var/lib/postgresql/backup
```
- `-Fp` — формат plain (сегменты файлов)
- `-Xs` — архив WAL-файлов включать с индексами
- `-P` — показывать прогресс

### 2.3 Автоматизация (cron)

Пример crontab для ежедневного копирования в 02:00 ночи:
```bash
0 2 * * * PGPASSWORD=118100 pg_dump -U dba_admin -h localhost -p 5432 -d servicedesk | gzip > /var/backups/sd_db_$(date +\%Y\%m\%d).sql.gz
```
Файлы резервных копий должны храниться на отдельном диске/томе, отличном от данных БД.

## 3. Восстановление из резервной копии

### 3.1 Полное восстановление

```bash
# 1. Стоп контейнер (опционально)
docker compose stop postgres

# 2. Очистка данных
docker compose exec -T postgres bash -c "rm -rf /var/lib/postgresql/data/*

# 3. Восстановление из дампа
PGPASSWORD=118100 psql -U dba_admin -d servicedesk < backup_20261020.sql

# 4. Запуск контейнера
docker compose start postgres
```

### 3.2 Восстановление до точки времени (PITR)

Используется `pg_basebackup` + WAL архивы:
1. Восстанавливаем базу из `pg_basebackup`
2. Копируем WAL-архивы в `pg_wal/`
3. Настраиваем `recovery_target_time` или `recovery_target_in_command`
4. Запускаем postgres в режиме recovery

## 4. Мониторинг

### 4.1 pg_stat_activity (активные запросы)

```bash
# Показать все активные запросы с временем выполнения
docker compose exec -T postgres psql -U dba_admin -d servicedesk -c "
SELECT pid, state, query, wait_event_type, wait_event, 
       round(EXTRACT(EPOCH FROM query_start / 1000)::numeric, 1) AS query_age_s
FROM pg_stat_activity
WHERE state = 'active' AND pid <> pg_backend_pid()
ORDER BY query_age_s DESC;
"
```

### 4.2 pg_stat_replication (репликация)

```bash
docker compose exec -T postgres psql -U dba_admin -d servicedesk -c "
SELECT pid, state, client_addr, client_hostname, client_port,
       sync_state, sent_location, write_location, flush_location, replay_location
FROM pg_stat_replication;
```

### 4.3 Размер таблиц и индексов

```bash
docker compose exec -T postgres psql -U dba_admin -d servicedesk -c "
SELECT n.nspname || '.' || c.relname AS tbl,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total,
       pg_size_pretty(pg_relation_size(c.oid))       AS data,
       pg_size_pretty(pg_indexes_size(c.oid))        AS idx
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname IN ('core', 'ref', 'sec', 'audit')
ORDER BY pg_total_relation_size(c.oid) DESC;
"
```

### 4.4 Журналы PostgreSQL

Логи находятся в контейнере по пути `/var/log/postgresql/postgresql-16*.log`.
Просмотр последних 50 строк:
```bash
docker compose exec -T postgres tail -50 /var/log/postgresql/postgresql-16.log
```

### 4.5 Пользовательские алерты (на базе pg_sensor / мониторинга)

Например, оповещение при превышении времени выполнения запроса:
```bash
# Создать расширение pg_stat_statements если еще не создано
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Топ-5 самых тяжелых запросов за последнюю минуту
SELECT query, calls, total_exec_time, 100.0 * total_exec_time / nullsum(calls) AS avg_ms
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 5;
```

## 5. Регламентные задачи

### 5.1 VACUUM (очистка мертвых строк)

PostgreSQL автоматически работает с мертвыми строками благодаря MVCC, но периодический VACUUM необходим:

```bash
# Автоматический VACUUM (включен по умолчанию)
-- Настроить через parameters в postgresql.conf или через ALTER DATABASE:
ALTER DATABASE servicedesk SET vacuum_scale_factor = 0.05;
ALTER DATABASE servicedesk SET vacuum_threshold = 50;

# Ручной VACUUM FULL (только при простоянии системы!):
docker compose exec -T postgres vacuumdb -U dba_admin -d servicedesk -f --analyze
```

**VACUUM FULL** перезаписывает таблицу и пересоздает индексы — требует exclusive lock, поэтому делается только при простоянии или в ночные часы.

### 5.2 ANALYZE (обновление статистики)

```bash
# Автоматический ANALYZE (включен по умолчанию через cron)
docker compose exec -T postgres analyze_vacuum( analyze_only := true );

# Или вручную:
docker compose exec -T postgres psql -U dba_admin -d servicedesk -c "ANALYZE core.tickets;"
```

### 5.3 Cron (планировщик задач)

Пример crontab для ежедневных операций:

```bash
# Ежедневно в 03:00 — анализ таблиц
0 3 * * * docker compose exec -T postgres psql -U dba_admin -d servicedesk -c "ANALYZE;" >> /var/log/sd_db_analyze.log 2>&1

# Еженедельно в воскресенье в 04:00 — VACUUM FULL (только при простоянии)
0 4 * * 0 docker compose exec -T postgres vacuumdb -U dba_admin -d servicedesk -f >> /var/log/sd_db_vacuum.log 2>&1
```

## 6. Настройка PostgreSQL (tuning)

### 5.3.1 postgresql.conf (выделенные параметры)

Ключевые параметры для Service Desk (1М строк):

| Параметр | Значение | Комментарий |
|---|---|---|
| `work_mem` | `256MB` | Для сортировок и хэш-aggregation. Высокое значение ускоряет запросы, но 소모ет RAM. |
| `maintenance_work_mem` | `256MB` | Для VACUUM и CREATE INDEX. |
| `max_parallel_workers_per_gather` | `4` | Параллельное выполнение запросов. |
| `max_parallel_workers` | `4` | Общее число рабочих процессов. |
| `max_parallel_maintenance_workers` | `2` | Для параллельного VACUUM/REINDEX. |
| `effective_cache_size` | `1GB` | Помогает планировщику оценивать стоимость сканов. |

**Применение изменений:**
```bash
# Через psql (требует restart)
ALTER SYSTEM SET work_mem = '256MB';
SELECT pg_reload_conf();
```

### 5.3.2 pg_hba.conf (авторизация)

Последняя строка файла (после всех остальных правил):
```
# TYPE  DATABASE  USER  ADDRESS  METHOD

# LDAP авторизация (настройка из п. 4.2)
host    servicedesk     all   ldap    ldapserver=ldap.service.desk \
    ldapbasedn=dc=service,dc=desk \
    ldapbinddn=cn=admin,dc=service,dc=desk \
    ldapbindpassword=118100 \
    ldapsearchattribute=uid \
    ldapremotefilter=(objectClass=inetOrgPerson) \
    ldapauthmethod=simple

# Локальная авторизация для суперпользователя
local   all             all                                     peer

# Доступ для dba_admin по сети
host    servicedesk     dba_admin  127.0.0.1/32  md5
```

### 5.3.3 Логирование

В `postgresql.conf`:
```sql
log_statement = 'all'        -- логировать все запросы
log_min_duration_statement = 1000  -- логировать запросы долей > 1 сек
log_connections = on         -- логировать подключения
log_disconnections = on      -- логировать отключения
log_lock_waits = on          -- логировать блокировки
```

## 6. Чек-лист администрирования (еженедельно)

- [ ] Проверить свободное место на диске (datadir, backup dir)
- [ ] Выполнить `ANALYZE` (или убедиться, что cron сработал)
- [ ] Проверить логи на ошибки (некорректные запросы, сбои соединений)
- [ ] Проверить размер логов иRotate при необходимости
- [ ] Проверить свободную память/диск в контейнерах
- [ ] Проверить статус контейнеров (`docker compose ps`)
- [ ] Проверить, что резервные копии создавались согласно расписанию
- [ ] Проверить, что паролиPG пользователей не истекают (если используется истечение пароля)

## 7. Частые задачи администрирования (примеры)

### Задача 1: Увеличение диска данных

Если таблицы растут и место заканчивается:
```bash
# Добавить место в卷 (через Docker Desktop или lvm)
# Перезапустить контейнер с новым объемом
docker compose down
# ... изменить размерVolume в docker-compose.yml или через Docker Desktop
docker compose up -d
# Восстановить из бэкапа, если нужно
```

### Задача 2: Устранение сбоев блокировок

```bash
# Показать активные блокировки
docker compose exec -T postgres psql -U dba_admin -d servicedesk -c "
SELECT pid, mode, relation::regclass, waiting, blocked_by
FROM pg_locks l1 JOIN pg_locks l2 ON l1.locked = l2.locked AND l1.pid != l2.pid;
"

# Убить зомби-процесс (осторожно!):
SELECT pg_terminate_backend(<pid>);
```

### Задача 3: Сброс последовательности ID

Если `ticket_number` или `history_id` вышли за пределы ожидаемого диапазона:
```bash
# Пример для sequences:
ALTER SEQUENCE core.ticket_number_seq RESTART WITH 1000001;
ALTER SEQUENCE core.ticket_status_history_seq RESTART WITH 500001;
```

---
*Документ подготовлен для п. 4.3 ТЗ. Содержит как теоретические основы, так и具体тельные команды для выполнения практики.*