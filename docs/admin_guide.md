# Руководство системного администратора (ПМ.02)

## 1. Развёртывание системы

### 1.1 Требования к серверу
- ОС: Linux (Ubuntu 22.04+) или Windows 10/11 с Docker Desktop
- RAM: не менее 4 ГБ (рекомендуется 8 ГБ)
- Диск: не менее 20 ГБ свободного места
- Docker: 24.x+ с поддержкой Compose v2

### 1.2 Установка
```bash
# 1. Клонировать репозиторий
git clone https://github.com/FireWolf435/Prakticheskaya.git
cd Prakticheskaya

# 2. Создать .env из шаблона
cp .env.example .env
# Отредактировать .env (пароли, домен)

# 3. Запустить систему
docker compose up -d

# 4. Проверить статус
docker compose ps
# Все контейнеры должны быть healthy
```

### 1.3 Инициализация БД
```bash
# Загрузка исходных данных
python importer/load_stage.py

# Трансформация в целевые таблицы
python importer/transform.py

# Обработка почты
python mailintake/intake.py

# Генерация нагрузочных данных
python analysis/gen_load.py 100000
```

## 2. Резервное копирование

### 2.1 Полное резервное копирование (pg_dump)
```bash
# С хоста
pg_dump -U "${POSTGRES_USER}" -h localhost -p "${POSTGRES_PORT:-5432}" -d "${POSTGRES_DB}" > backup_$(date +%Y%m%d).sql

# Через docker
docker compose exec -T postgres pg_dump -U dba_admin -d servicedesk > backup_$(date +%Y%m%d).sql

# С сжатием
docker compose exec -T postgres pg_dump -U dba_admin -d servicedesk | gzip > backup_$(date +%Y%m%d).sql.gz
```

### 2.2 Автоматическое резервное копирование (cron)
```bash
# Ежедневно в 02:00
0 2 * * * pg_dump -U "${POSTGRES_USER}" -h localhost -p "${POSTGRES_PORT:-5432}" -d "${POSTGRES_DB}" | gzip > /var/backups/sd_db_$(date +\%Y\%m\%d).sql.gz
```

### 2.3 Физическое копирование (pg_basebackup)
```bash
docker compose exec -T postgres pg_basebackup -U dba_admin -d servicedesk -Fp -Xs -P -D /var/lib/postgresql/backup
```

## 3. Восстановление

### 3.1 Полное восстановление
```bash
# 1. Остановить контейнер
docker compose stop postgres

# 2. Очистить данные
docker compose exec -T postgres bash -c "rm -rf /var/lib/postgresql/data/*"

# 3. Восстановить из дампа
psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" < backup_YYYYMMDD.sql

# 4. Запустить контейнер
docker compose start postgres
```

### 3.2 PITR (Point-in-Time Recovery)
1. Восстановить из pg_basebackup
2. Скопировать WAL-архивы в `pg_wal/`
3. Настроить `recovery_target_time` или `recovery_target_lsn`
4. Запустить postgres в режиме recovery

## 4. Ротация паролей

### 4.1 Сроки действия
- Пароли пользователей: 90 дней
- Служебные учётки: 90 дней
- Уведомление за 7 дней до истечения

### 4.2 Процедура смены пароля
```sql
-- Смена пароля пользователем
ALTER USER ivan PASSWORD 'NewPass123!';

-- Сброс пароля администратором
ALTER USER ivan PASSWORD 'TempPass123!' VALID UNTIL '2026-11-27';
```

### 4.3 Служебные учётки
- Пароли передаются через `.env` (не в репозитории)
- Ротация: обновить `.env` и перезапустить контейнеры
- Учётки: notifier, mailintake, app_service, migrator

## 5. Мониторинг

### 5.1 Проверка статуса контейнеров
```bash
docker compose ps
# Все контейнеры должны быть healthy
```

### 5.2 Проверка БД
```bash
# Активные запросы
docker compose exec -T postgres psql -U dba_admin -d servicedesk -c "
SELECT pid, state, query, wait_event_type, 
       round(EXTRACT(EPOCH FROM query_start / 1000)::numeric, 1) AS age_s
FROM pg_stat_activity
WHERE state = 'active' AND pid <> pg_backend_pid()
ORDER BY age_s DESC;
"

# Размер таблиц
docker compose exec -T postgres psql -U dba_admin -d servicedesk -c "
SELECT n.nspname || '.' || c.relname AS tbl,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname IN ('core', 'ref', 'sec', 'audit')
ORDER BY pg_total_relation_size(c.oid) DESC;
"
```

### 5.3 Проверка логов
```bash
docker compose exec -T postgres tail -50 /var/log/postgresql/postgresql-16.log
```

## 6. Регламентные задачи

### 6.1 Ежедневно (cron)
```bash
# 03:00 — анализ таблиц
0 3 * * * docker compose exec -T postgres psql -U dba_admin -d servicedesk -c "ANALYZE;"

# 04:00 — VACUUM FULL (только при простоянии)
0 4 * * 0 docker compose exec -T postgres vacuumdb -U dba_admin -d servicedesk -f
```

### 6.2 Еженедельно
- Проверить свободное место на диске
- Проверить логи на ошибки
- Проверить статус контейнеров
- Проверить, что резервные копии создавались

### 6.3 Ежемесячно
- Проверить покрытие тестов
- Проверить производительность запросов
- Обновить production_calendar на следующий год

## 7. Устранение сбоев

### 7.1 Блокировки
```sql
-- Показать активные блокировки
SELECT pid, mode, relation::regclass, waiting, blocked_by
FROM pg_locks l1 JOIN pg_locks l2 ON l1.locked = l2.locked AND l1.pid != l2.pid;

-- Убить зомби-процесс
SELECT pg_terminate_backend(<pid>);
```

### 7.2 Восстановление после сбоя контейнера
```bash
# Перезапустить контейнер
docker compose restart postgres

# Проверить логи
docker compose logs postgres --tail 50

# Проверить статус
docker compose ps
```

## 8. Чек-лист администрирования

- [ ] Проверить свободное место на диске
- [ ] Выполнить `ANALYZE` (или убедиться, что cron сработал)
- [ ] Проверить логи на ошибки
- [ ] Проверить статус контейнеров
- [ ] Проверить, что резервные копии создавались
- [ ] Проверить пароли (не истекают ли)
- [ ] Проверить LDAP-синхронизацию (dirsync)

---

*Руководство системного администратора подготовлено для п. 4.3 и п. 4.4 ТЗ.*
